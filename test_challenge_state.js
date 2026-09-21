"use strict";

const assert = require("node:assert/strict");
const {
  applyLoadedChallenge,
  createRequestGate,
  findResumePosition,
  isPublicHistoryDate,
  responseMatchesRequest,
} = require("./static/challenge_state.js");

const categoryNames = [
  "Higher Score",
  "More Popular",
  "More Episodes",
  "More Recent",
];

function challenge(date, titlePrefix, firstId) {
  return {
    challenge_date: date,
    public_history_start: "2026-09-19",
    comparison_stats: [{ category: "Higher Score", comparison_position: 1 }],
    categories: categoryNames.map((name, categoryIndex) => ({
      name,
      anime: Array.from({ length: 6 }, (_, animeIndex) => ({
        mal_id: firstId + categoryIndex * 10 + animeIndex,
        title: `${titlePrefix} ${categoryIndex + 1}-${animeIndex + 1}`,
      })),
    })),
  };
}

function dirtyState() {
  return {
    challenge: null,
    roundIndex: 3,
    comparisonIndex: 4,
    totalScore: 12,
    roundScore: 3,
    answer: { correct: true },
    revealPhase: "resolved",
    waitingForAnswer: true,
    transitioning: true,
    revealedMetrics: new Map([["old", { score: 9 }]]),
    selections: [{ category: "Old", comparison_position: 1 }],
    reviewEntries: [{ category: "Old" }],
    comparisonStats: [{ category: "Old" }],
    completion: { verified_score: 12 },
    challengeRequestDate: "old",
    playingArchivedChallenge: false,
    officialAttempt: false,
    selectedArchiveDate: "2026-09-01",
    lastGameplayScreen: {},
    publicHistoryStart: null,
  };
}

const archive = challenge("2026-09-19", "Archive Anime", 1000);
const today = challenge("2026-09-21", "Today Anime", 2000);
const state = dirtyState();

applyLoadedChallenge(state, archive, {
  requestDate: "2026-09-19",
  mode: "archive",
  officialAttempt: true,
});
assert.equal(state.challenge.categories[0].anime[0].title, "Archive Anime 1-1");
assert.equal(state.playingArchivedChallenge, true);

state.totalScore = 4;
state.roundIndex = 1;
state.comparisonIndex = 3;
state.selections.push({ category: "More Popular", comparison_position: 4 });
state.revealedMetrics.set("archive", { members: 10 });
applyLoadedChallenge(state, today, {
  requestDate: "today",
  mode: "today",
  officialAttempt: true,
});
assert.equal(state.challenge.challenge_date, "2026-09-21");
assert.equal(state.challenge.categories[0].anime[0].title, "Today Anime 1-1");
assert.equal(
  state.challenge.categories.flatMap((category) => category.anime)
    .some((anime) => anime.title.startsWith("Archive Anime")),
  false,
);
assert.equal(state.roundIndex, 0);
assert.equal(state.comparisonIndex, 0);
assert.equal(state.totalScore, 0);
assert.equal(state.roundScore, 0);
assert.equal(state.selections.length, 0);
assert.equal(state.reviewEntries.length, 0);
assert.equal(state.revealedMetrics.size, 0);
assert.equal(state.answer, null);
assert.equal(state.completion, null);
assert.equal(state.playingArchivedChallenge, false);
assert.equal(state.challengeRequestDate, "today");

const answers = [
  { category: "Higher Score", comparison_position: 1, correct: true },
  { category: "Higher Score", comparison_position: 2, correct: false },
];
assert.deepEqual(findResumePosition(today, answers), {
  complete: false,
  roundIndex: 0,
  comparisonIndex: 2,
  roundScore: 1,
});

applyLoadedChallenge(state, archive, {
  requestDate: "2026-09-19",
  mode: "archive",
  officialAttempt: true,
});
assert.equal(state.challenge.challenge_date, "2026-09-19");
assert.equal(state.challenge.categories[0].anime[0].title, "Archive Anime 1-1");
assert.equal(state.totalScore, 0);

const requests = createRequestGate();
const archiveRequest = requests.begin();
const todayRequest = requests.begin();
assert.equal(requests.isCurrent(todayRequest), true);
assert.equal(requests.isCurrent(archiveRequest), false);

assert.equal(responseMatchesRequest(today, "today", "2026-09-21", false), true);
assert.equal(responseMatchesRequest(archive, "today", "2026-09-21", false), false);
assert.equal(responseMatchesRequest(archive, "2026-09-19", "2026-09-21", false), true);
assert.equal(isPublicHistoryDate("2026-09-18", "2026-09-19"), false);
assert.equal(isPublicHistoryDate("2026-09-19", "2026-09-19"), true);

console.log("challenge navigation state checks passed");
