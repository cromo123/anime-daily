(function exposeChallengeState(globalScope, factory) {
  const api = factory();
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
  globalScope.AniMoredleChallengeState = api;
})(typeof globalThis === "undefined" ? this : globalThis, function buildChallengeState() {
  function createRequestGate() {
    let latestRequest = 0;

    return {
      begin() {
        latestRequest += 1;
        return latestRequest;
      },
      isCurrent(requestId) {
        return requestId === latestRequest;
      },
    };
  }

  function responseMatchesRequest(challenge, requestDate, localDate, playtestMode) {
    if (playtestMode) return Boolean(challenge?.playtest_id);
    const expectedDate = requestDate === "today" ? localDate : requestDate;
    return challenge?.challenge_date === expectedDate;
  }

  function isPublicHistoryDate(challengeDate, publicHistoryStart) {
    return !publicHistoryStart || challengeDate >= publicHistoryStart;
  }

  function shouldShowTodayLanding(state, localDate, playtestMode) {
    return Boolean(
      !playtestMode &&
      state.challenge &&
      state.challenge.challenge_date === localDate &&
      !state.playingArchivedChallenge &&
      !state.completion,
    );
  }

  function dailyChallengeButtonLabel(answerCount) {
    return answerCount > 0
      ? "Resume Daily Challenge"
      : "Start Daily Challenge";
  }

  function createSingleUseGate() {
    let used = false;

    return {
      enter() {
        if (used) return false;
        used = true;
        return true;
      },
      reset() {
        used = false;
      },
    };
  }

  function prepareNextRound(state, totalRounds) {
    if (state.roundIndex >= totalRounds - 1) return false;
    state.roundIndex += 1;
    state.comparisonIndex = 0;
    state.roundScore = 0;
    state.answer = null;
    state.revealPhase = null;
    state.transitioning = false;
    return true;
  }

  function roundTransitionDetails(challenge, roundIndex, roundScore, comparisonsPerRound) {
    const completedRound = challenge.categories[roundIndex];
    const nextRound = challenge.categories[roundIndex + 1];
    if (!completedRound || !nextRound) return null;
    return {
      completedRoundNumber: roundIndex + 1,
      completedCategory: completedRound.name,
      completedScore: `${roundScore} / ${comparisonsPerRound}`,
      nextCategory: nextRound.name,
      nextQuestion: nextRound.question,
    };
  }

  function malEntryUrl(malId) {
    return Number.isSafeInteger(malId) && malId > 0
      ? `https://myanimelist.net/anime/${malId}` : null;
  }

  function initialChallengeDate(requested, localDate) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(requested || "")) return "today";
    const parsed = new Date(`${requested}T00:00:00Z`);
    return !Number.isNaN(parsed.getTime()) &&
      parsed.toISOString().slice(0, 10) === requested && requested <= localDate
      ? requested : "today";
  }

  function applyLoadedChallenge(state, challenge, context) {
    state.challenge = challenge;
    state.roundIndex = 0;
    state.comparisonIndex = 0;
    state.totalScore = 0;
    state.roundScore = 0;
    state.answer = null;
    state.revealPhase = null;
    state.waitingForAnswer = false;
    state.transitioning = false;
    state.revealedMetrics = new Map();
    state.selections = [];
    state.reviewEntries = [];
    state.comparisonStats = challenge.comparison_stats || [];
    state.completion = null;
    state.challengeRequestDate = context.requestDate;
    state.playingArchivedChallenge = context.mode === "archive";
    state.officialAttempt = context.officialAttempt;
    state.selectedArchiveDate = context.mode === "archive"
      ? challenge.challenge_date
      : null;
    state.lastGameplayScreen = null;
    if (challenge.public_history_start) {
      state.publicHistoryStart = challenge.public_history_start;
    }
  }

  function findResumePosition(challenge, answers) {
    const answeredKeys = new Set(
      answers.map(
        (answer) => `${answer.category}:${answer.comparison_position}`,
      ),
    );

    for (let roundIndex = 0; roundIndex < challenge.categories.length; roundIndex += 1) {
      const category = challenge.categories[roundIndex];
      for (let position = 1; position < category.anime.length; position += 1) {
        if (!answeredKeys.has(`${category.name}:${position}`)) {
          return {
            complete: false,
            roundIndex,
            comparisonIndex: position - 1,
            roundScore: answers
              .filter((answer) => answer.category === category.name)
              .reduce((score, answer) => score + (answer.correct ? 1 : 0), 0),
          };
        }
      }
    }

    return { complete: true };
  }

  return {
    applyLoadedChallenge,
    createSingleUseGate,
    createRequestGate,
    dailyChallengeButtonLabel,
    findResumePosition,
    isPublicHistoryDate,
    initialChallengeDate,
    malEntryUrl,
    prepareNextRound,
    responseMatchesRequest,
    roundTransitionDetails,
    shouldShowTodayLanding,
  };
});
