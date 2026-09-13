from crewai import Agent, Crew, LLM, Process, Task
from dotenv import load_dotenv
from pydantic import BaseModel, Field


DEFAULT_EDITOR_MODEL = "openai/gpt-5.6-luna"
NO_HISTORY_MESSAGE = "No prior stored challenges are available."


class EditorialBrief(BaseModel):
    target_popularity: float = Field(ge=0, le=100)
    target_difficulty: float = Field(ge=0, le=100)
    target_modernity: float = Field(ge=0, le=100)
    wildcard_target: int = Field(ge=0, le=3)
    notes: list[str]


def build_editorial_prompt(recent_history_context):
    history_text = recent_history_context.strip() or NO_HISTORY_MESSAGE

    return f"""
Create an editorial brief for the NEXT Anime Daily challenge.

RECENT STORED CHALLENGES:

{history_text}

The next challenge should still strongly favor recognizable anime, but daily
challenges should not all feel statistically identical.

Choose targets that create useful variation from recent days while still fitting
Anime Daily's identity.

Definitions:

target_popularity:
0-100. Higher means a more mainstream/recognizable lineup.
Anime Daily generally strongly favors popular anime, so do not make this low
just for the sake of variety.

target_difficulty:
0-100. Higher means comparisons with closer factual values and therefore
harder decisions.

target_modernity:
0-100. Higher means the lineup skews toward newer releases.

wildcard_target:
Number from 0 to 3 representing approximately how many genuinely obscure
surprise entries should appear across the full 30-anime challenge.

notes:
A few short editorial directions for the downstream generator and curator.

These are SOFT TARGETS, not factual rules.
Python remains responsible for eligibility, validity, uniqueness, runtime rules,
history avoidance, and factual correctness.

Do not choose individual anime.
""".strip()


def run_editorial_planner(
    recent_history_context,
    llm=None,
    verbose=True,
):
    if llm is None:
        load_dotenv()
        llm = LLM(model=DEFAULT_EDITOR_MODEL)

    editor = Agent(
        role="Anime Daily Editor",
        goal="Plan varied and entertaining daily challenges over time.",
        backstory=(
            "Anime Daily is a More-or-Less style anime game with five "
            "categories: Higher MAL Score, More Popular, More Episodes, More "
            "Recent, and Longer Runtime. Each daily challenge contains 30 anime "
            "across 25 comparisons. Most anime should be recognizable, with "
            "occasional obscure surprises. You do not choose individual anime "
            "and you do not determine factual answers. Your job at this stage is "
            "to decide what editorial character the next daily challenge should "
            "have."
        ),
        llm=llm,
        verbose=verbose,
    )
    task = Task(
        description=build_editorial_prompt(recent_history_context),
        expected_output=(
            "A structured EditorialBrief for the next daily challenge."
        ),
        output_pydantic=EditorialBrief,
        agent=editor,
    )
    crew = Crew(
        agents=[editor],
        tasks=[task],
        process=Process.sequential,
    )
    result = crew.kickoff()

    if result.pydantic is None:
        raise RuntimeError("The editorial planner did not return a valid brief.")

    return EditorialBrief.model_validate(result.pydantic)
