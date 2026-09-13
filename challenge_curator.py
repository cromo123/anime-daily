from crewai import Agent, Crew, LLM, Process, Task
from dotenv import load_dotenv
from pydantic import BaseModel


DEFAULT_CURATOR_MODEL = "openai/gpt-5.6-luna"


class CuratorDecision(BaseModel):
    candidate_id: str
    reason_tags: list[str]
    summary: str


def format_brief_for_curator(editorial_brief):
    notes = "\n".join(f"- {note}" for note in editorial_brief.notes)

    if not notes:
        notes = "- No additional editorial notes."

    return "\n".join(
        [
            f"Target popularity: {editorial_brief.target_popularity}",
            f"Target difficulty: {editorial_brief.target_difficulty}",
            f"Target modernity: {editorial_brief.target_modernity}",
            f"Wildcard target: {editorial_brief.wildcard_target}",
            "Editorial notes:",
            notes,
        ]
    )


def build_curator_prompt(
    shortlisted_candidates,
    editorial_brief,
    candidate_context,
):
    if not shortlisted_candidates:
        raise ValueError("At least one shortlisted candidate is required.")

    return f"""
Review the following {len(shortlisted_candidates)} VALID Anime Daily candidate
challenges and select exactly one supplied candidate ID.

PLANNER BRIEF:

{format_brief_for_curator(editorial_brief)}

The numeric brief is editorial guidance, not a hard requirement. Python has
already shortlisted these candidates for closeness to the numeric targets. Your
job is now to judge subjective lineup quality. You may prefer a slightly less
numerically perfect candidate when its titles, eras, franchise mix, surprises,
or overall game quality make it a better daily challenge.

The ratings are all on a 0-100 scale:

- Popularity: higher means the anime are generally more recognizable.
- Difficulty: higher means factual comparison values are closer together.
- Modernity: higher means the lineup skews toward newer releases.

Do not calculate factual winners or factual values. Do not question whether the
candidates are valid. Python already guarantees factual correctness, eligibility,
tie prevention, uniqueness, and matchup validity.

Prefer a recognizable core, interesting surprises, varied eras and franchises,
and engaging rather than uniformly trivial comparisons. Avoid excessive
obscurity, sequel clutter, repetitive franchises, and an unbalanced lineup.

CANDIDATES:

{candidate_context}
""".strip()


def run_challenge_curator(
    shortlisted_candidates,
    editorial_brief,
    candidate_context,
    llm=None,
    verbose=True,
):
    if llm is None:
        load_dotenv()
        llm = LLM(model=DEFAULT_CURATOR_MODEL)

    curator = Agent(
        role="Anime Daily Challenge Curator",
        goal="Choose the most entertaining candidate challenge for Anime Daily.",
        backstory=(
            "Anime Daily is a More-or-Less style game with four categories and "
            "five chained comparisons per category. Most selected anime should "
            "be recognizable, with occasional obscure or surprising picks. A "
            "good daily challenge has variety across eras and franchises and is "
            "neither uniformly obvious nor frustratingly difficult. Python owns "
            "all factual correctness; your responsibility is purely editorial."
        ),
        llm=llm,
        verbose=verbose,
    )
    task = Task(
        description=build_curator_prompt(
            shortlisted_candidates,
            editorial_brief,
            candidate_context,
        ),
        expected_output=(
            "A structured decision selecting exactly one supplied candidate."
        ),
        output_pydantic=CuratorDecision,
        agent=curator,
    )
    crew = Crew(
        agents=[curator],
        tasks=[task],
        process=Process.sequential,
    )
    result = crew.kickoff()

    if result.pydantic is None:
        raise RuntimeError("The curator did not return a valid decision.")

    return CuratorDecision.model_validate(result.pydantic)
