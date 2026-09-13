from datetime import date

from crewai import Agent, Crew, LLM, Process, Task
from dotenv import load_dotenv
from pydantic import BaseModel

from challenge import generate_challenge_candidates
from curator_context import (
    build_curator_candidate_context,
    format_curator_candidate_context,
)


load_dotenv()


class CuratorDecision(BaseModel):
    candidate_id: str
    reason_tags: list[str]
    summary: str


llm = LLM(
    model="openai/gpt-5.6-luna",
)


# 1. Generate twenty real candidate challenges.
candidates = generate_challenge_candidates(
    challenge_date=date.today().isoformat(),
    count=20,
)

# 2. Strip them down to editorial information.
curator_context = build_curator_candidate_context(candidates)


# 3. Turn that structure into concise text for the LLM.
candidate_text = format_curator_candidate_context(curator_context)


curator = Agent(
    role="Anime Daily Challenge Curator",
    goal="Choose the most entertaining candidate challenge for Anime Daily.",
    backstory=(
        "Anime Daily is a More-or-Less style game with four categories: "
        "Higher MAL Score, More Popular, More Episodes, and More Recent. "
        "Each category contains six anime forming five chained comparisons. "
        "Most selected anime should be recognizable and popular, but occasional obscure "
        "or surprising picks are desirable. A good daily challenge should have variety "
        "across eras and franchises and should not be uniformly obvious, impossibly "
        "difficult, excessively obscure, or dominated by only new anime. "
        "Python already guarantees factual correctness, eligibility, tie prevention, "
        "and matchup validity. Your responsibility is purely editorial."
    ),
    llm=llm,
    verbose=True,
)


task = Task(
    description=f"""
Review the following VALID Anime Daily candidate challenges.

The ratings are all on a 0-100 scale:

Popularity:
Higher means the selected anime are generally more recognizable/popular.

Difficulty:
Higher means the factual comparisons are expected to be harder because the
compared values are closer together.

Modernity:
Higher means the selected anime skew toward newer releases.

Do not calculate factual winners.
Do not question whether the candidates are valid.
Python already handles those responsibilities.

Choose the candidate that you believe would make the most entertaining daily game.

Prefer:
- a strong recognizable core
- some surprising or less obvious selections
- interesting difficulty rather than every comparison being trivial
- variety across eras
- variety across franchises
- a lineup that feels interesting as a complete daily challenge

Avoid:
- excessive obscurity
- excessive sequel/spinoff clutter when possible
- repetitive franchise representation
- uniformly trivial comparisons
- difficulty so extreme that the game becomes frustrating

CANDIDATES:

{candidate_text}
""",
    expected_output="A structured decision selecting exactly one supplied candidate.",
    output_pydantic=CuratorDecision,
    agent=curator,
)


crew = Crew(
    agents=[curator],
    tasks=[task],
    process=Process.sequential,
)

result = crew.kickoff()

decision = result.pydantic

valid_candidate_ids = {
    candidate["candidate_id"]
    for candidate in candidates
}

if decision.candidate_id not in valid_candidate_ids:
    raise ValueError(
        f"Curator selected unknown candidate: {decision.candidate_id}"
    )

print("\nSELECTED CANDIDATE:")
print(decision.candidate_id)

print("\nREASON TAGS:")
for tag in decision.reason_tags:
    print("-", tag)

print("\nSUMMARY:")
print(decision.summary)
