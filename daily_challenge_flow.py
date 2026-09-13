"""Create or load one stored daily challenge through editorial curation."""

import sqlite3
from datetime import date
from typing import Literal

from crewai.flow import Flow, listen, router, start
from pydantic import BaseModel, Field

from candidate_shortlisting import (
    DEFAULT_CANDIDATE_POOL_SIZE,
    DEFAULT_SHORTLIST_LIMIT,
    generate_brief_guided_shortlist,
)
from challenge import challenge_signature, load_stored_challenge
from challenge_curator import CuratorDecision, run_challenge_curator
from curator_context import (
    build_curator_candidate_context,
    format_curator_candidate_context,
)
from editorial_planner import (
    EditorialBrief,
    NO_HISTORY_MESSAGE,
    run_editorial_planner,
)
from planner_history import (
    format_recent_challenge_history,
    load_recent_challenge_history,
)
from database import load_challenge_record, record_challenge


CANDIDATE_POOL_SIZE = DEFAULT_CANDIDATE_POOL_SIZE
SHORTLIST_SIZE = DEFAULT_SHORTLIST_LIMIT
RECENT_HISTORY_COUNT = 7


class AnimeDailyFlowState(BaseModel):
    challenge_date: str = ""
    history_text: str = ""
    brief: EditorialBrief | None = None
    shortlisted_candidates: list[dict] = Field(default_factory=list)
    decision: CuratorDecision | None = None
    selected_candidate: dict | None = None
    stored_challenge: list[dict] | None = None
    challenge_id: int | None = None
    status: Literal["existing", "newly_generated"] | None = None
    playtest: bool = False


class DailyChallengeFlow(Flow[AnimeDailyFlowState]):
    @classmethod
    def for_date(cls, challenge_date=None):
        """Create a flow for today or an explicit YYYY-MM-DD date."""
        target_date = (
            date.today()
            if challenge_date is None
            else date.fromisoformat(challenge_date)
        )
        return cls(
            initial_state=AnimeDailyFlowState(
                challenge_date=target_date.isoformat()
            ),
            suppress_flow_events=True,
        )

    @classmethod
    def for_playtest(cls, challenge_date=None):
        """Run curation without loading or recording a daily challenge."""
        flow = cls.for_date(challenge_date)
        flow.state.playtest = True
        return flow

    @start()
    def check_existing(self):
        if not self.state.challenge_date:
            self.state.challenge_date = date.today().isoformat()

        if self.state.playtest:
            return "challenge_checked"

        stored_challenge = load_stored_challenge(self.state.challenge_date)
        if stored_challenge is not None:
            self.state.stored_challenge = stored_challenge
            self.state.challenge_id = load_challenge_record(
                self.state.challenge_date
            )["id"]
            self.state.status = "existing"

        return "challenge_checked"

    @router(check_existing, emit=["existing", "missing"])
    def route_challenge(self, previous_result):
        if self.state.stored_challenge is not None:
            return "existing"
        return "missing"

    @listen("existing")
    def return_existing(self):
        print(f"Existing challenge loaded for {self.state.challenge_date}.")
        return self.state.stored_challenge

    @listen("missing")
    def load_history(self):
        recent_history = load_recent_challenge_history(
            self.state.challenge_date,
            count=RECENT_HISTORY_COUNT,
        )
        self.state.history_text = (
            format_recent_challenge_history(recent_history)
            or NO_HISTORY_MESSAGE
        )
        print(f"Loaded {len(recent_history)} prior stored challenge(s).")
        return "history_loaded"

    @listen(load_history)
    def run_planner(self, previous_result):
        self.state.brief = run_editorial_planner(
            self.state.history_text,
            verbose=False,
        )
        brief = self.state.brief
        print(
            "Planner targets: "
            f"P {brief.target_popularity} | "
            f"D {brief.target_difficulty} | "
            f"M {brief.target_modernity} | "
            f"wildcards {brief.wildcard_target}"
        )
        return "planner_complete"

    @listen(run_planner)
    def generate_candidates(self, previous_result):
        brief = self.state.brief
        if brief is None:
            raise RuntimeError("The planner brief is missing from Flow state.")

        generated = generate_brief_guided_shortlist(
            challenge_date=self.state.challenge_date,
            target_popularity=brief.target_popularity,
            target_difficulty=brief.target_difficulty,
            target_modernity=brief.target_modernity,
            wildcard_target=brief.wildcard_target,
            pool_size=CANDIDATE_POOL_SIZE,
            limit=SHORTLIST_SIZE,
            include_pool=True,
        )
        candidate_pool = generated["candidate_pool"]
        shortlist = generated["shortlist"]
        pool_ids = {candidate["candidate_id"] for candidate in candidate_pool}
        pool_signatures = {
            challenge_signature(candidate["categories"])
            for candidate in candidate_pool
        }

        if (
            len(candidate_pool) != CANDIDATE_POOL_SIZE
            or len(pool_ids) != CANDIDATE_POOL_SIZE
            or len(pool_signatures) != CANDIDATE_POOL_SIZE
        ):
            raise RuntimeError(
                "The candidate pool is not "
                f"{CANDIDATE_POOL_SIZE} unique challenges."
            )

        if len(shortlist) != SHORTLIST_SIZE or any(
            candidate["candidate_id"] not in pool_ids for candidate in shortlist
        ):
            raise RuntimeError("The shortlist is incomplete or outside its pool.")

        self.state.shortlisted_candidates = shortlist
        print(
            f"Candidates: {len(candidate_pool)} generated, "
            f"{len(shortlist)} shortlisted."
        )
        return "candidates_ready"

    @listen(generate_candidates)
    def run_curator(self, previous_result):
        if self.state.brief is None:
            raise RuntimeError("The planner brief is missing from Flow state.")

        if len(self.state.shortlisted_candidates) != SHORTLIST_SIZE:
            raise RuntimeError("The shortlist is missing from Flow state.")

        curator_context = build_curator_candidate_context(
            self.state.shortlisted_candidates
        )
        candidate_text = format_curator_candidate_context(curator_context)
        self.state.decision = run_challenge_curator(
            self.state.shortlisted_candidates,
            self.state.brief,
            candidate_text,
            verbose=False,
        )
        print(f"Curator selected: {self.state.decision.candidate_id}")
        return "curator_complete"

    @listen(run_curator)
    def validate_selection(self, previous_result):
        if self.state.decision is None:
            raise RuntimeError("The curator decision is missing from Flow state.")

        candidates_by_id = {
            candidate["candidate_id"]: candidate
            for candidate in self.state.shortlisted_candidates
        }
        selected_id = self.state.decision.candidate_id

        if selected_id not in candidates_by_id:
            raise ValueError(
                "Curator selected a candidate outside the shortlist: "
                f"{selected_id}"
            )

        self.state.selected_candidate = candidates_by_id[selected_id]
        print(f"Selection validated: {selected_id}")
        return "selection_validated"

    @router(validate_selection, emit=["persist", "playtest"])
    def route_selection(self, previous_result):
        return "playtest" if self.state.playtest else "persist"

    @listen("playtest")
    def return_playtest(self):
        if self.state.selected_candidate is None:
            raise RuntimeError("The selected candidate is missing from Flow state.")
        print("Playtest selected without persistence.")
        return self.state.selected_candidate["categories"]

    @listen("persist")
    def persist_selection(self, previous_result):
        selected_candidate = self.state.selected_candidate
        if selected_candidate is None:
            raise RuntimeError("The selected candidate is missing from Flow state.")

        selected_challenge = selected_candidate["categories"]
        try:
            challenge_id = record_challenge(
                selected_challenge,
                self.state.challenge_date,
            )
        except sqlite3.IntegrityError:
            # Another process may have stored this date during curation.
            stored_challenge = load_stored_challenge(self.state.challenge_date)
            if stored_challenge is None:
                raise
            self.state.stored_challenge = stored_challenge
            self.state.challenge_id = load_challenge_record(
                self.state.challenge_date
            )["id"]
            self.state.status = "existing"
            print("Another process stored this date first; using its challenge.")
            return stored_challenge

        stored_challenge = load_stored_challenge(self.state.challenge_date)
        if stored_challenge is None:
            raise RuntimeError("The recorded challenge could not be reloaded.")

        selected_names = [category["name"] for category in selected_challenge]
        stored_names = [category["name"] for category in stored_challenge]
        if (
            stored_names != selected_names
            or challenge_signature(stored_challenge)
            != challenge_signature(selected_challenge)
        ):
            raise RuntimeError("Stored challenge differs from the selected candidate.")

        self.state.challenge_id = challenge_id
        self.state.stored_challenge = stored_challenge
        self.state.status = "newly_generated"
        print(f"Challenge {challenge_id} stored and reloaded.")
        return stored_challenge
