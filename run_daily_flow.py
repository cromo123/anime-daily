"""Create or load a daily challenge through the editorial Flow."""

from argparse import ArgumentParser

from daily_challenge_flow import DailyChallengeFlow


def main(challenge_date=None):
    flow = DailyChallengeFlow.for_date(challenge_date)
    stored_challenge = flow.kickoff()

    if stored_challenge != flow.state.stored_challenge:
        raise RuntimeError("Flow result does not match the stored challenge.")

    print("\nDAILY FLOW RESULT")
    print("Date:", flow.state.challenge_date)
    print("Status:", flow.state.status)
    if flow.state.challenge_id is not None:
        print("Challenge ID:", flow.state.challenge_id)

    if flow.state.status == "newly_generated":
        brief = flow.state.brief
        decision = flow.state.decision
        print(
            "Planner targets: "
            f"P {brief.target_popularity} | "
            f"D {brief.target_difficulty} | "
            f"M {brief.target_modernity} | "
            f"wildcards {brief.wildcard_target}"
        )
        print("Curator candidate:", decision.candidate_id)
        print("Curator reason tags:", ", ".join(decision.reason_tags))


if __name__ == "__main__":
    parser = ArgumentParser(description="Create or load a daily challenge.")
    parser.add_argument("challenge_date", nargs="?", help="YYYY-MM-DD (default: today)")
    arguments = parser.parse_args()
    main(arguments.challenge_date)
