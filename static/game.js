const TOTAL_ROUNDS = 4;
const COMPARISONS_PER_ROUND = 5;
const TOTAL_COMPARISONS = TOTAL_ROUNDS * COMPARISONS_PER_ROUND;
const INTRO_DURATION_MS = 1200;
const CARD_TRANSITION_MS = 420;
const CHALLENGER_ENTER_MS = 360;
const REVEAL_DURATION_MS = 2000;
const RESULT_PAUSE_MS = 700;
const PLAYTEST_MODE =
  new URLSearchParams(window.location.search).get("playtest") === "1";
const {
  applyLoadedChallenge,
  createSingleUseGate,
  createRequestGate,
  dailyChallengeButtonLabel,
  findResumePosition,
  isPublicHistoryDate,
  prepareNextRound,
  responseMatchesRequest,
  roundTransitionDetails,
  shouldShowMobileAd,
  shouldShowTodayLanding,
} = window.AniMoredleChallengeState;

const state = {
  challenge: null,
  roundIndex: 0,
  comparisonIndex: 0,
  totalScore: 0,
  roundScore: 0,
  answer: null,
  revealPhase: null,
  waitingForAnswer: false,
  transitioning: false,
  revealedMetrics: new Map(),
  selections: [],
  reviewEntries: [],
  comparisonStats: [],
  runSequence: 0,
  completion: null,
  challengeRequestDate: "today",
  playingArchivedChallenge: false,
  archiveYear: new Date().getFullYear(),
  archiveMonth: new Date().getMonth() + 1,
  archiveData: null,
  selectedArchiveDate: null,
  retryGeneratePlaytest: false,
  visibleScreen: null,
  lastGameplayScreen: null,
  officialAttempt: false,
  publicHistoryStart: null,
};

let introSequence = 0;
let mobileAdDismissed = false;
const navigationRequests = createRequestGate();
const roundContinueGate = createSingleUseGate();
let audioContext = null;
const feedbackSounds = new Set();
const countingSounds = new Set();
let revealFrameId = null;
let revealResolve = null;
let resultPauseId = null;
let resultPauseResolve = null;

const elements = {
  brand: document.querySelector(".brand"),
  loadingScreen: document.querySelector("#loading-screen"),
  errorScreen: document.querySelector("#error-screen"),
  landingScreen: document.querySelector("#landing-screen"),
  roundIntro: document.querySelector("#round-intro"),
  roundCompleteScreen: document.querySelector("#round-complete-screen"),
  gameScreen: document.querySelector("#game-screen"),
  resultsScreen: document.querySelector("#results-screen"),
  reviewScreen: document.querySelector("#review-screen"),
  archiveScreen: document.querySelector("#archive-screen"),
  archiveResultScreen: document.querySelector("#archive-result-screen"),
  desktopAdSlot: document.querySelector("#desktop-ad-slot"),
  mobileAdSlot: document.querySelector("#mobile-ad-slot"),
  mobileAdDismiss: document.querySelector("#mobile-ad-dismiss"),
  resultsAdSlot: document.querySelector("#results-ad-slot"),
  roundTransitionAdSlot: document.querySelector(".round-transition-ad-slot"),
  challengeDate: document.querySelector("#challenge-date"),
  challengeLabel: document.querySelector("#challenge-label"),
  todayNavButton: document.querySelector("#today-nav-button"),
  archiveNavButton: document.querySelector("#archive-nav-button"),
  introRound: document.querySelector("#intro-round"),
  introTitle: document.querySelector("#intro-title"),
  introQuestion: document.querySelector("#intro-question"),
  roundCompleteLabel: document.querySelector("#round-complete-label"),
  roundCompleteCategory: document.querySelector("#round-complete-category"),
  roundCompleteScore: document.querySelector("#round-complete-score"),
  nextCategoryName: document.querySelector("#next-category-name"),
  nextCategoryQuestion: document.querySelector("#next-category-question"),
  roundContinueButton: document.querySelector("#round-continue-button"),
  categoryCount: document.querySelector("#category-count"),
  categoryName: document.querySelector("#category-name"),
  categoryQuestion: document.querySelector("#category-question"),
  comparisonLabel: document.querySelector("#comparison-label"),
  roundScoreCount: document.querySelector("#round-score-count"),
  animeCards: document.querySelector("#anime-cards"),
  requestError: document.querySelector("#request-error"),
  retryCompletionButton: document.querySelector("#retry-completion-button"),
  reviewButton: document.querySelector("#review-button"),
  reviewReturnButton: document.querySelector("#review-return-button"),
  reviewList: document.querySelector("#review-list"),
  retryLoadButton: document.querySelector("#retry-load-button"),
  loadErrorMessage: document.querySelector("#load-error-message"),
  finalScore: document.querySelector("#final-score"),
  finalPercentage: document.querySelector("#final-percentage"),
  finalStanding: document.querySelector("#final-standing"),
  resultsCopy: document.querySelector("#results-copy"),
  resultsEyebrow: document.querySelector("#results-eyebrow"),
  resultsTitle: document.querySelector("#results-title"),
  replayButton: document.querySelector("#replay-button"),
  newPlaytestButton: document.querySelector("#new-playtest-button"),
  resultsArchiveButton: document.querySelector("#results-archive-button"),
  archiveMonthTitle: document.querySelector("#archive-month-title"),
  calendarGrid: document.querySelector("#calendar-grid"),
  archiveError: document.querySelector("#archive-error"),
  previousMonthButton: document.querySelector("#previous-month-button"),
  nextMonthButton: document.querySelector("#next-month-button"),
  archiveResultDate: document.querySelector("#archive-result-date"),
  archiveResultScore: document.querySelector("#archive-result-score"),
  archiveResultPercentage: document.querySelector("#archive-result-percentage"),
  archiveResultCopy: document.querySelector("#archive-result-copy"),
  practiceButton: document.querySelector("#practice-button"),
  archiveReturnButton: document.querySelector("#archive-return-button"),
  landingChallengeDate: document.querySelector("#landing-challenge-date"),
  landingStartButton: document.querySelector("#landing-start-button"),
};

function adScreenName(screen) {
  if (screen === elements.landingScreen) return "landing";
  if (screen === elements.gameScreen) return "game";
  if (screen === elements.roundCompleteScreen) return "transition";
  if (screen === elements.resultsScreen) return "results";
  if (screen === elements.loadingScreen) return "loading";
  if (screen === elements.errorScreen) return "error";
  return "other";
}

function updateAdVisibility(screen) {
  const showMobileAd = shouldShowMobileAd(
    adScreenName(screen),
    mobileAdDismissed,
    PLAYTEST_MODE,
  );
  elements.mobileAdSlot.hidden = !showMobileAd;
  document.body.classList.toggle("has-mobile-ad", showMobileAd);
  elements.desktopAdSlot.hidden = PLAYTEST_MODE;
  elements.roundTransitionAdSlot.hidden = PLAYTEST_MODE;
  elements.resultsAdSlot.hidden =
    PLAYTEST_MODE || state.playingArchivedChallenge;
}

function showScreen(screen) {
  state.visibleScreen = screen;
  if (
    screen === elements.roundIntro ||
    screen === elements.roundCompleteScreen ||
    screen === elements.gameScreen ||
    screen === elements.resultsScreen ||
    screen === elements.reviewScreen
  ) {
    state.lastGameplayScreen = screen;
  }
  for (const candidate of [
    elements.loadingScreen,
    elements.errorScreen,
    elements.landingScreen,
    elements.roundIntro,
    elements.roundCompleteScreen,
    elements.gameScreen,
    elements.resultsScreen,
    elements.reviewScreen,
    elements.archiveScreen,
    elements.archiveResultScreen,
  ]) {
    candidate.hidden = candidate !== screen;
  }
  updateAdVisibility(screen);
}

function dismissMobileAd() {
  mobileAdDismissed = true;
  elements.mobileAdSlot.hidden = true;
  document.body.classList.remove("has-mobile-ad");
}

function prefersReducedMotion() {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

function wait(milliseconds) {
  const duration = prefersReducedMotion() ? 0 : milliseconds;
  return new Promise((resolve) => window.setTimeout(resolve, duration));
}

function prepareAudio() {
  try {
    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    if (!AudioContextClass) return;
    audioContext ||= new AudioContextClass();
    if (audioContext.state === "suspended") audioContext.resume().catch(() => {});
  } catch {
    // Audio feedback is optional and must never block an answer.
  }
}

function playAnswerSound(correct) {
  if (!audioContext || audioContext.state !== "running") return;
  try {
    const start = audioContext.currentTime;
    const notes = correct ? [[660, 0], [880, 0.09]] : [[330, 0], [260, 0.11]];
    for (const [frequency, offset] of notes) {
      const oscillator = audioContext.createOscillator();
      const gain = audioContext.createGain();
      oscillator.type = correct ? "sine" : "triangle";
      oscillator.frequency.setValueAtTime(frequency, start + offset);
      gain.gain.setValueAtTime(0.0001, start + offset);
      gain.gain.exponentialRampToValueAtTime(0.055, start + offset + 0.015);
      gain.gain.exponentialRampToValueAtTime(0.0001, start + offset + 0.15);
      oscillator.connect(gain).connect(audioContext.destination);
      feedbackSounds.add(oscillator);
      oscillator.onended = () => feedbackSounds.delete(oscillator);
      oscillator.start(start + offset);
      oscillator.stop(start + offset + 0.16);
    }
  } catch {
    // Missing audio support does not affect gameplay.
  }
}

function playCountingTick() {
  if (!audioContext || audioContext.state !== "running" || prefersReducedMotion()) return;
  try {
    const oscillator = audioContext.createOscillator();
    const gain = audioContext.createGain();
    const start = audioContext.currentTime;
    oscillator.type = "sine";
    oscillator.frequency.setValueAtTime(430, start);
    gain.gain.setValueAtTime(0.0001, start);
    gain.gain.exponentialRampToValueAtTime(0.018, start + 0.006);
    gain.gain.exponentialRampToValueAtTime(0.0001, start + 0.045);
    oscillator.connect(gain).connect(audioContext.destination);
    oscillator.start(start);
    oscillator.stop(start + 0.05);
    countingSounds.add(oscillator);
    oscillator.onended = () => countingSounds.delete(oscillator);
  } catch {
    // Counting feedback is optional.
  }
}

function stopCountingTicks() {
  for (const oscillator of countingSounds) {
    try { oscillator.stop(); } catch { /* Already stopped. */ }
  }
  countingSounds.clear();
}

function cancelPendingFeedback() {
  if (revealFrameId !== null) window.cancelAnimationFrame(revealFrameId);
  revealFrameId = null;
  revealResolve?.();
  revealResolve = null;
  if (resultPauseId !== null) window.clearTimeout(resultPauseId);
  resultPauseId = null;
  resultPauseResolve?.();
  resultPauseResolve = null;
  for (const oscillator of feedbackSounds) {
    try { oscillator.stop(); } catch { /* Already stopped. */ }
  }
  feedbackSounds.clear();
  stopCountingTicks();
}

function pauseAfterResult() {
  return new Promise((resolve) => {
    resultPauseResolve = resolve;
    resultPauseId = window.setTimeout(() => {
      resultPauseId = null;
      resultPauseResolve = null;
      resolve();
    }, RESULT_PAUSE_MS);
  });
}

function formatDate(value) {
  const parsedDate = new Date(`${value}T00:00:00`);

  if (Number.isNaN(parsedDate.getTime())) {
    return value;
  }

  return new Intl.DateTimeFormat(undefined, {
    year: "numeric",
    month: "long",
    day: "numeric",
  }).format(parsedDate);
}

function formatNumber(value, maximumFractionDigits = 0) {
  return new Intl.NumberFormat(undefined, { maximumFractionDigits }).format(value);
}

function localDateString() {
  const now = new Date();
  const year = now.getFullYear();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function setActiveNavigation(activeView) {
  const todayIsActive = activeView === "today";
  elements.todayNavButton.classList.toggle("is-active", todayIsActive);
  elements.archiveNavButton.classList.toggle("is-active", !todayIsActive);
  elements.todayNavButton.toggleAttribute("aria-current", todayIsActive);
  elements.archiveNavButton.toggleAttribute("aria-current", !todayIsActive);
}

function showTodayLanding() {
  elements.landingChallengeDate.dateTime = state.challenge.challenge_date;
  elements.landingChallengeDate.textContent = formatDate(
    state.challenge.challenge_date,
  );
  elements.landingStartButton.textContent = dailyChallengeButtonLabel(
    state.selections.length,
  );
  setActiveNavigation("today");
  showScreen(elements.landingScreen);
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function restoreTodayView() {
  const today = localDateString();
  if (
    !state.challenge ||
    (!PLAYTEST_MODE && (
      state.playingArchivedChallenge || state.challenge.challenge_date !== today
    ))
  ) {
    loadChallenge(PLAYTEST_MODE ? "playtest" : "today");
    return;
  }

  navigationRequests.begin();
  state.officialAttempt = !PLAYTEST_MODE;
  state.playingArchivedChallenge = false;
  elements.challengeLabel.textContent = PLAYTEST_MODE ? "PLAYTEST" : "Daily challenge";
  elements.challengeDate.textContent = formatDate(state.challenge.challenge_date);
  setActiveNavigation("today");
  if (shouldShowTodayLanding(state, today, PLAYTEST_MODE)) {
    showTodayLanding();
  } else if (state.completion) {
    showScreen(elements.resultsScreen);
  } else {
    showScreen(state.lastGameplayScreen || elements.gameScreen);
  }
}

function validateChallenge(challenge) {
  return (
    challenge &&
    Array.isArray(challenge.categories) &&
    challenge.categories.length === TOTAL_ROUNDS &&
    challenge.categories.every(
      (category) =>
        Array.isArray(category.anime) &&
        category.anime.length === COMPARISONS_PER_ROUND + 1,
    )
  );
}

function restoreOfficialProgress(progress) {
  if (!progress || !Array.isArray(progress.answers) || !progress.answers.length) {
    return false;
  }

  for (const answer of progress.answers) {
    const categoryIndex = state.challenge.categories.findIndex(
      (category) => category.name === answer.category,
    );
    if (categoryIndex < 0) continue;
    const category = state.challenge.categories[categoryIndex];
    const leftAnime = category.anime[answer.comparison_position - 1];
    const rightAnime = category.anime[answer.comparison_position];
    state.revealedMetrics.set(
      `${categoryIndex}:${answer.revealed_anime[0].mal_id}`,
      answer.revealed_anime[0],
    );
    state.revealedMetrics.set(
      `${categoryIndex}:${answer.revealed_anime[1].mal_id}`,
      answer.revealed_anime[1],
    );
    state.reviewEntries.push({
      category: answer.category,
      position: answer.comparison_position,
      leftAnime: { mal_id: leftAnime.mal_id, title: leftAnime.title },
      rightAnime: { mal_id: rightAnime.mal_id, title: rightAnime.title },
      answer,
    });
    state.selections.push({
      category: answer.category,
      comparison_position: answer.comparison_position,
      selected_mal_id: answer.selected_mal_id,
    });
  }
  state.totalScore = progress.score || 0;
  const resume = findResumePosition(state.challenge, progress.answers);
  if (resume.complete) return true;
  state.roundIndex = resume.roundIndex;
  state.comparisonIndex = resume.comparisonIndex;
  state.roundScore = resume.roundScore;
  return false;
}

function progressSignature(progress) {
  return (progress?.answers || [])
    .map(
      (answer) =>
        `${answer.category}:${answer.comparison_position}:${answer.selected_mal_id}`,
    )
    .sort()
    .join("|");
}

function challengeSignature(challenge) {
  return (challenge?.categories || [])
    .map(
      (category) =>
        `${category.name}:${(category.anime || [])
          .map((anime) => anime.mal_id)
          .join(",")}`,
    )
    .join("|");
}

async function resyncOfficialProgress() {
  if (
    PLAYTEST_MODE ||
    !state.challenge ||
    state.visibleScreen === elements.archiveScreen ||
    state.visibleScreen === elements.archiveResultScreen ||
    state.playingArchivedChallenge ||
    state.challenge.challenge_date !== localDateString()
  ) {
    return;
  }
  const requestId = navigationRequests.begin();

  try {
    const response = await fetch(
      `/challenge/today?local_date=${encodeURIComponent(localDateString())}`,
      { headers: { Accept: "application/json" }, cache: "no-store" },
    );
    const authoritative = await readJsonResponse(response);
    if (!navigationRequests.isCurrent(requestId)) return;
    const today = localDateString();
    const localProgress = {
      answers: state.selections.map((selection) => selection),
    };
    const serverProgress = authoritative.progress || {};
    if (
      authoritative.challenge_date !== today ||
      challengeSignature(state.challenge) !== challengeSignature(authoritative) ||
      progressSignature(localProgress) !== progressSignature(serverProgress) ||
      state.totalScore !== (serverProgress.score || 0)
    ) {
      await loadChallenge("today");
    }
  } catch {
    if (!navigationRequests.isCurrent(requestId)) return;
    if (!state.challenge || state.challenge.challenge_date !== localDateString()) {
      elements.loadErrorMessage.textContent =
        "Today's official challenge is unavailable right now.";
      showScreen(elements.loadErrorScreen);
    }
  }
}

async function readJsonResponse(response) {
  const data = await response.json().catch(() => null);

  if (!response.ok) {
    const message = data?.detail || `Request failed with status ${response.status}.`;
    throw new Error(message);
  }

  return data;
}

async function loadChallenge(challengeDate = "today", generateNewPlaytest = false) {
  cancelPendingFeedback();
  const sequence = ++introSequence;
  const requestId = navigationRequests.begin();
  state.runSequence += 1;
  state.challengeRequestDate = challengeDate;
  state.retryGeneratePlaytest = generateNewPlaytest;
  showScreen(elements.loadingScreen);

  try {
    if (
      !PLAYTEST_MODE &&
      challengeDate !== "today" &&
      !isPublicHistoryDate(challengeDate, state.publicHistoryStart)
    ) {
      throw new Error("This challenge is not part of the public archive.");
    }

    let response;
    if (PLAYTEST_MODE) {
      response = await fetch(
        generateNewPlaytest ? "/dev/playtest/generate" : "/dev/playtest",
        {
          method: generateNewPlaytest ? "POST" : "GET",
          headers: { Accept: "application/json" },
        },
      );
      if (response.status === 404 && !generateNewPlaytest) {
        response = await fetch("/dev/playtest/generate", {
          method: "POST",
          headers: { Accept: "application/json" },
        });
      }
      if (response.status === 404) {
        throw new Error(
          "Playtest mode is unavailable. Enable ANIME_DAILY_DEV_MODE=true on the server.",
        );
      }
    } else {
      const challengePath =
        challengeDate === "today"
          ? `/challenge/today?local_date=${encodeURIComponent(localDateString())}`
          : `/challenge/${encodeURIComponent(challengeDate)}`;
      response = await fetch(challengePath, {
        headers: { Accept: "application/json" },
      });
    }
    const challenge = await readJsonResponse(response);

    if (sequence !== introSequence || !navigationRequests.isCurrent(requestId)) return;

    if (
      !validateChallenge(challenge) ||
      !responseMatchesRequest(
        challenge,
        challengeDate,
        localDateString(),
        PLAYTEST_MODE,
      )
    ) {
      throw new Error("The daily challenge data is incomplete.");
    }

    cancelPendingFeedback();
    state.runSequence += 1;
    elements.reviewList.replaceChildren();
    elements.retryCompletionButton.hidden = true;
    const mode = PLAYTEST_MODE
      ? "playtest"
      : challengeDate === "today"
        ? "today"
        : "archive";
    applyLoadedChallenge(state, challenge, {
      requestDate: challengeDate,
      mode,
      officialAttempt: !PLAYTEST_MODE,
    });
    const progressComplete = !PLAYTEST_MODE && restoreOfficialProgress(challenge.progress);
    elements.challengeDate.textContent = formatDate(challenge.challenge_date);
    if (PLAYTEST_MODE) {
      elements.challengeLabel.textContent = "PLAYTEST";
    } else {
      elements.challengeLabel.textContent = state.playingArchivedChallenge
        ? "Archive challenge"
        : "Daily challenge";
    }
    setActiveNavigation(state.playingArchivedChallenge ? "archive" : "today");
    if (progressComplete) {
      showResults({
        verified_score: state.totalScore,
        verified_percentage: state.totalScore / TOTAL_COMPARISONS * 100,
        official_score: state.totalScore,
        total_questions: TOTAL_COMPARISONS,
        replay: false,
      });
    } else if (mode === "today") {
      showTodayLanding();
    } else {
      showRoundIntro();
    }
  } catch (error) {
    if (sequence !== introSequence || !navigationRequests.isCurrent(requestId)) return;
    elements.loadErrorMessage.textContent =
      error.message || "Check your connection and try again.";
    showScreen(elements.errorScreen);
  }
}

function currentRound() {
  return state.challenge.categories[state.roundIndex];
}

function currentAnimePair() {
  const anime = currentRound().anime;
  return [
    anime[state.comparisonIndex],
    anime[state.comparisonIndex + 1],
  ];
}

function revealKey(malId) {
  return `${state.roundIndex}:${malId}`;
}

function rememberRevealedMetrics(answer) {
  for (const revealedAnime of answer.revealed_anime) {
    state.revealedMetrics.set(revealKey(revealedAnime.mal_id), revealedAnime);
  }
}

function revealedMetricFor(anime) {
  return state.revealedMetrics.get(revealKey(anime.mal_id));
}

async function showRoundIntro() {
  const sequence = ++introSequence;
  const round = currentRound();

  elements.introRound.textContent = `Round ${state.roundIndex + 1} / ${TOTAL_ROUNDS}`;
  elements.introTitle.textContent = round.name;
  elements.introQuestion.textContent = round.question;
  showScreen(elements.roundIntro);

  await wait(INTRO_DURATION_MS);

  if (sequence !== introSequence) {
    return;
  }

  showScreen(elements.gameScreen);
  renderComparison();
}

function showRoundComplete() {
  const details = roundTransitionDetails(
    state.challenge,
    state.roundIndex,
    state.roundScore,
    COMPARISONS_PER_ROUND,
  );
  if (!details) return;

  elements.roundCompleteLabel.textContent = `Round ${details.completedRoundNumber} complete`;
  elements.roundCompleteCategory.textContent = details.completedCategory;
  elements.roundCompleteScore.textContent = details.completedScore;
  elements.nextCategoryName.textContent = details.nextCategory;
  elements.nextCategoryQuestion.textContent = details.nextQuestion;
  elements.roundContinueButton.textContent = `Continue to ${details.nextCategory}`;
  elements.roundContinueButton.disabled = false;
  roundContinueGate.reset();
  state.transitioning = false;
  showScreen(elements.roundCompleteScreen);
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function continueToNextRound() {
  if (
    state.visibleScreen !== elements.roundCompleteScreen ||
    !roundContinueGate.enter()
  ) {
    return;
  }

  elements.roundContinueButton.disabled = true;
  state.transitioning = true;
  if (!prepareNextRound(state, TOTAL_ROUNDS)) {
    state.transitioning = false;
    elements.roundContinueButton.disabled = false;
    roundContinueGate.reset();
    return;
  }
  showRoundIntro();
}

function startOrResumeTodayChallenge() {
  if (
    state.lastGameplayScreen === elements.roundCompleteScreen &&
    state.comparisonIndex === COMPARISONS_PER_ROUND - 1 &&
    state.answer
  ) {
    showRoundComplete();
    return;
  }
  showRoundIntro();
}

function createCover(anime) {
  const coverWrap = document.createElement("div");
  coverWrap.className = "cover-wrap";

  const placeholder = document.createElement("div");
  placeholder.className = "cover-placeholder";
  placeholder.textContent = "Cover unavailable";

  if (!anime.image_url) {
    coverWrap.append(placeholder);
    return coverWrap;
  }

  const image = document.createElement("img");
  image.className = "cover-image";
  image.src = anime.image_url;
  image.alt = `${anime.title} cover`;
  image.loading = "eager";
  placeholder.hidden = true;

  image.addEventListener("error", () => {
    image.remove();
    placeholder.hidden = false;
  });

  coverWrap.append(image, placeholder);
  return coverWrap;
}

function formatReveal(categoryName, revealedAnime) {
  if (categoryName === "Higher Score") {
    return `Score: ${formatNumber(revealedAnime.score, 2)}`;
  }

  if (categoryName === "More Popular") {
    const rank = formatNumber(revealedAnime.popularity_rank);
    const members = formatNumber(revealedAnime.members);
    return `Popularity rank: #${rank} · ${members} members`;
  }

  if (categoryName === "More Episodes") {
    return `${formatNumber(revealedAnime.series_episodes)} series episodes`;
  }

  if (categoryName === "More Recent") {
    return `Released: ${revealedAnime.release_date}`;
  }

  return "";
}

function animatedReveal(categoryName, anime, progress) {
  const eased = 1 - (1 - progress) ** 3;
  if (categoryName === "Higher Score") {
    const score = Math.min(anime.score * eased, anime.score - 0.01);
    return `Score: ${formatNumber(Math.max(0, score), 2)}`;
  }
  if (categoryName === "More Popular") {
    const members = Math.min(Math.floor(anime.members * eased), anime.members - 1);
    return `Popularity rank: #— · ${formatNumber(Math.max(0, members))} members`;
  }
  if (categoryName === "More Episodes") {
    const episodes = Math.min(
      Math.floor(anime.series_episodes * eased), anime.series_episodes - 1,
    );
    return `${formatNumber(Math.max(0, episodes))} series episodes`;
  }
  if (categoryName === "More Recent") {
    const year = Number(anime.release_date.slice(0, 4));
    return `Released: ${Math.min(Math.floor(year - 20 + 20 * eased), year - 1)}`;
  }
  return formatReveal(categoryName, anime);
}

function animateRevealedValues(answer, sequence, alreadyRevealedIds) {
  const cards = [...elements.animeCards.children];
  const categoryName = currentRound().name;
  if (prefersReducedMotion()) return Promise.resolve();

  const metrics = cards.filter(
    (card) => !alreadyRevealedIds.has(Number(card.dataset.malId)),
  ).map((card) => ({
    element: card.querySelector(".metric-reveal"),
    anime: answer.revealed_anime.find(
      (item) => item.mal_id === Number(card.dataset.malId),
    ),
  }));
  if (!metrics.length) return Promise.resolve();
  for (const metric of metrics) {
    metric.element.textContent = animatedReveal(categoryName, metric.anime, 0);
  }

  return new Promise((resolve) => {
    revealResolve = resolve;
    let startedAt;
    function frame(now) {
      if (sequence !== state.runSequence) {
        revealFrameId = null;
        revealResolve = null;
        stopCountingTicks();
        resolve();
        return;
      }
      startedAt ??= now;
      const progress = Math.min((now - startedAt) / REVEAL_DURATION_MS, 1);
      for (const metric of metrics) {
        const nextText = progress === 1
          ? formatReveal(categoryName, metric.anime)
          : animatedReveal(categoryName, metric.anime, progress);
        if (metric.element.textContent !== nextText) {
          metric.element.textContent = nextText;
          playCountingTick();
        }
      }
      if (progress < 1) {
        revealFrameId = window.requestAnimationFrame(frame);
      } else {
        revealFrameId = null;
        revealResolve = null;
        stopCountingTicks();
        resolve();
      }
    }
    revealFrameId = window.requestAnimationFrame(frame);
  });
}

function addMetricReveal(cardCopy, revealedAnime, initialText = null) {
  const metric = document.createElement("p");
  metric.className = "metric-reveal";
  metric.textContent = initialText ?? formatReveal(currentRound().name, revealedAnime);
  cardCopy.append(metric);
}

function addChoiceMarker(card) {
  const marker = document.createElement("span");
  marker.className = "card-verdict";
  marker.textContent = "Your choice";
  card.append(marker);
}

function createAnimeCard(anime, entering = false) {
  const card = document.createElement("button");
  card.className = "anime-card";
  card.type = "button";
  card.dataset.malId = String(anime.mal_id);
  card.setAttribute("aria-label", `Choose ${anime.title}`);
  card.disabled =
    state.waitingForAnswer || Boolean(state.answer) || state.transitioning;

  if (entering) {
    card.classList.add("chain-enter-right");
  }

  const cardCopy = document.createElement("div");
  cardCopy.className = "card-copy";

  const title = document.createElement("h2");
  title.className = "anime-title";
  title.textContent = anime.title;

  cardCopy.append(title);
  if (currentRound().name === "More Episodes") {
    const seriesContext = document.createElement("div");
    seriesContext.className = "series-context";
    for (const text of ["+ All existing sequels", "Full anime series"]) {
      const seriesLabel = document.createElement("p");
      seriesLabel.className = "series-label";
      seriesLabel.textContent = text;
      seriesContext.append(seriesLabel);
    }
    cardCopy.append(seriesContext);
  }
  card.append(createCover(anime), cardCopy);

  if (state.answer) {
    const revealedAnime = state.answer.revealed_anime.find(
      (revealed) => revealed.mal_id === anime.mal_id,
    );
    const wasSelected = anime.mal_id === state.answer.selected_mal_id;
    const carriedReveal = revealedMetricFor(anime);

    if (state.revealPhase === "resolved") {
      if (anime.mal_id === state.answer.correct_mal_id) {
        card.classList.add("is-correct");
      } else if (wasSelected) {
        card.classList.add("is-incorrect");
      } else {
        card.classList.add("is-dimmed");
      }
    } else if (wasSelected) {
      card.classList.add("is-pending-choice");
    } else if (carriedReveal) {
      card.classList.add("is-carried");
    }

    if (wasSelected) {
      addChoiceMarker(card);
    }
    const initialText = state.revealPhase === "suspense" && !carriedReveal &&
      !prefersReducedMotion()
      ? animatedReveal(currentRound().name, revealedAnime, 0)
      : null;
    addMetricReveal(cardCopy, carriedReveal || revealedAnime, initialText);
  } else {
    const carriedReveal = revealedMetricFor(anime);

    if (carriedReveal) {
      card.classList.add("is-carried");
      addMetricReveal(cardCopy, carriedReveal);
    }

    card.addEventListener("click", () => submitAnswer(anime.mal_id));
  }

  return card;
}

function updateRoundStatus() {
  const round = currentRound();

  elements.categoryCount.textContent = `Round ${state.roundIndex + 1} / ${
    TOTAL_ROUNDS
  }`;
  elements.categoryName.textContent = round.name;
  elements.categoryQuestion.textContent = round.question;
  elements.comparisonLabel.textContent = `Comparison ${
    state.comparisonIndex + 1
  } / ${COMPARISONS_PER_ROUND}`;
  elements.roundScoreCount.textContent = String(state.roundScore);
}

function comparisonStat(category, position) {
  return state.comparisonStats.find(
    (stat) =>
      stat.category === category && stat.comparison_position === position,
  );
}

function renderComparison() {
  const animePair = currentAnimePair();

  updateRoundStatus();
  elements.requestError.hidden = true;
  elements.retryCompletionButton.hidden = true;
  elements.animeCards.replaceChildren(
    createAnimeCard(animePair[0]),
    createAnimeCard(animePair[1]),
  );

}

function setChoicesDisabled(disabled) {
  for (const card of elements.animeCards.querySelectorAll(".anime-card")) {
    card.disabled = disabled;
  }
}

async function submitAnswer(selectedMalId) {
  if (state.waitingForAnswer || state.answer || state.transitioning) {
    return;
  }

  state.waitingForAnswer = true;
  const sequence = state.runSequence;
  prepareAudio();
  setChoicesDisabled(true);
  const selectedCard = [...elements.animeCards.children].find(
    (card) => Number(card.dataset.malId) === selectedMalId,
  );
  selectedCard.classList.add("is-pending-choice");
  addChoiceMarker(selectedCard);
  elements.requestError.hidden = true;

  try {
    const answerPath = !state.officialAttempt
      ? `/dev/playtest/${encodeURIComponent(state.challenge.playtest_id)}/answer`
      : `/challenge/${encodeURIComponent(state.challenge.challenge_date)}/answer`;
    const response = await fetch(answerPath, {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        category: currentRound().name,
        comparison_position: state.comparisonIndex + 1,
        selected_mal_id: selectedMalId,
      }),
    });
    const answer = await readJsonResponse(response);
    if (sequence !== state.runSequence) return;

    const [leftAnime, rightAnime] = currentAnimePair();
    state.reviewEntries.push({
      category: currentRound().name,
      position: state.comparisonIndex + 1,
      leftAnime: { mal_id: leftAnime.mal_id, title: leftAnime.title },
      rightAnime: { mal_id: rightAnime.mal_id, title: rightAnime.title },
      answer,
    });

    const alreadyRevealedIds = new Set(
      [leftAnime, rightAnime]
        .filter((anime) => revealedMetricFor(anime))
        .map((anime) => anime.mal_id),
    );
    state.answer = answer;
    if (answer.comparison_stats) {
      state.comparisonStats = [
        ...state.comparisonStats.filter(
          (stat) =>
            !(
              stat.category === answer.category &&
              stat.comparison_position === answer.comparison_position
            ),
        ),
        answer.comparison_stats,
      ];
    }
    state.revealPhase = "suspense";
    state.selections.push({
      category: currentRound().name,
      comparison_position: state.comparisonIndex + 1,
      selected_mal_id: selectedMalId,
    });

    renderComparison();
    await animateRevealedValues(answer, sequence, alreadyRevealedIds);
    if (sequence !== state.runSequence) return;

    rememberRevealedMetrics(answer);
    state.revealPhase = "resolved";
    if (answer.correct) {
      state.totalScore += 1;
      state.roundScore += 1;
    }
    renderComparison();
    playAnswerSound(answer.correct);
    await pauseAfterResult();
    if (sequence === state.runSequence) await advanceGame();
  } catch (error) {
    if (sequence !== state.runSequence) return;
    selectedCard.classList.remove("is-pending-choice");
    selectedCard.querySelector(".card-verdict")?.remove();
    elements.requestError.textContent = `${
      error.message || "The answer could not be checked."
    } Please try again.`;
    elements.requestError.hidden = false;
    setChoicesDisabled(false);
  } finally {
    if (sequence === state.runSequence) {
      state.waitingForAnswer = false;
    }
  }
}

function prepareCarriedCard(card, anime) {
  card.className = "anime-card is-carried";
  card.style.removeProperty("--chain-shift-x");
  card.style.removeProperty("--chain-shift-y");
  card.dataset.malId = String(anime.mal_id);
  card.setAttribute("aria-label", `Choose ${anime.title}`);
  card.disabled = true;

  card.querySelector(".card-verdict")?.remove();
  card.addEventListener("click", () => submitAnswer(anime.mal_id));
}

async function animateToNextComparison() {
  const sequence = state.runSequence;
  state.transitioning = true;

  const [leftCard, rightCard] = elements.animeCards.children;
  const leftRect = leftCard.getBoundingClientRect();
  const rightRect = rightCard.getBoundingClientRect();

  rightCard.style.setProperty(
    "--chain-shift-x",
    `${leftRect.left - rightRect.left}px`,
  );
  rightCard.style.setProperty(
    "--chain-shift-y",
    `${leftRect.top - rightRect.top}px`,
  );
  leftCard.classList.add("chain-exit-left");
  rightCard.classList.add("chain-carry-left");

  await wait(CARD_TRANSITION_MS);
  if (sequence !== state.runSequence) return;

  leftCard.remove();
  state.comparisonIndex += 1;
  state.answer = null;
  state.revealPhase = null;
  elements.requestError.hidden = true;

  const [carriedAnime, incomingAnime] = currentAnimePair();
  prepareCarriedCard(rightCard, carriedAnime);
  const incomingCard = createAnimeCard(incomingAnime, true);
  elements.animeCards.append(incomingCard);
  updateRoundStatus();

  await wait(CHALLENGER_ENTER_MS);
  if (sequence !== state.runSequence) return;

  incomingCard.classList.remove("chain-enter-right");
  state.transitioning = false;
  setChoicesDisabled(false);
}

async function animateRoundExit() {
  const sequence = state.runSequence;
  state.transitioning = true;

  for (const card of elements.animeCards.children) {
    card.classList.add("round-exit");
  }

  await wait(CHALLENGER_ENTER_MS);
  return sequence === state.runSequence;
}

function showResults(completion) {
  if (PLAYTEST_MODE) {
    elements.finalScore.textContent = `${state.totalScore} / ${TOTAL_COMPARISONS}`;
    elements.finalPercentage.textContent = `${formatNumber(
      state.totalScore / TOTAL_COMPARISONS * 100,
      2,
    )}% correct`;
    elements.finalStanding.textContent = "Practice results are not ranked";
    elements.resultsCopy.textContent =
      "Practice only. This playtest does not change your daily results.";
    elements.resultsEyebrow.textContent = "PLAYTEST COMPLETE";
    elements.resultsTitle.textContent = "That’s all 20.";
    elements.replayButton.textContent = "Replay this playtest";
    elements.newPlaytestButton.hidden = false;
    elements.resultsArchiveButton.hidden = true;
    state.transitioning = false;
    showScreen(elements.resultsScreen);
    window.scrollTo({ top: 0, behavior: "smooth" });
    return;
  }

  elements.finalScore.textContent = `${completion.verified_score} / ${completion.total_questions}`;
  elements.finalPercentage.textContent = `${formatNumber(
    completion.verified_percentage,
    2,
  )}% correct`;
  state.comparisonStats = completion.comparison_stats || state.comparisonStats;
  elements.finalStanding.textContent = completion.top_percent
    ? `Top ${completion.top_percent}% of players`
    : "Standing unavailable until more players complete this challenge";

  if (completion.replay) {
    elements.resultsCopy.textContent =
      `Replay complete. Your official result remains ${completion.official_score} ` +
      `/ ${completion.total_questions}.`;
  } else {
    elements.resultsCopy.textContent =
      "Your verified score is saved as this challenge’s official result.";
  }

  elements.resultsEyebrow.textContent = state.playingArchivedChallenge
    ? formatDate(state.challenge.challenge_date)
    : "Daily challenge complete";
  elements.resultsTitle.textContent = state.playingArchivedChallenge
    ? "Archive challenge complete."
    : "That’s today’s 20.";
  elements.replayButton.textContent = state.playingArchivedChallenge
    ? "Replay as practice"
    : "Replay today’s challenge";
  elements.resultsArchiveButton.hidden = !state.playingArchivedChallenge;

  state.completion = completion;
  state.transitioning = false;
  showScreen(elements.resultsScreen);
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function makeReviewSide(anime, revealed, entry) {
  const side = document.createElement("div");
  side.className = "review-side";
  if (anime.mal_id === entry.answer.correct_mal_id) side.classList.add("is-correct");
  if (anime.mal_id === entry.answer.selected_mal_id && !entry.answer.correct) {
    side.classList.add("is-incorrect");
  }

  const labels = document.createElement("div");
  labels.className = "review-labels";
  if (anime.mal_id === entry.answer.selected_mal_id) {
    const choice = document.createElement("span");
    choice.textContent = "Your choice";
    labels.append(choice);
  }
  if (anime.mal_id === entry.answer.correct_mal_id) {
    const winner = document.createElement("span");
    winner.textContent = "Correct answer";
    labels.append(winner);
  }

  const title = document.createElement("strong");
  title.textContent = anime.title;
  const metric = document.createElement("span");
  metric.className = "review-metric";
  metric.textContent = formatReveal(entry.category, revealed);
  side.append(labels, title, metric);
  return side;
}

function openReview() {
  const sections = [];
  for (let roundIndex = 0; roundIndex < TOTAL_ROUNDS; roundIndex += 1) {
    const roundEntries = state.reviewEntries.slice(
      roundIndex * COMPARISONS_PER_ROUND,
      (roundIndex + 1) * COMPARISONS_PER_ROUND,
    );
    if (!roundEntries.length) continue;
    const section = document.createElement("section");
    section.className = "review-round";
    const heading = document.createElement("h2");
    heading.textContent = `Round ${roundIndex + 1} / ${TOTAL_ROUNDS} · ${roundEntries[0].category}`;
    section.append(heading);

    for (const entry of roundEntries) {
      const item = document.createElement("article");
      item.className = "review-item";
      const status = document.createElement("p");
      status.className = `review-status ${entry.answer.correct ? "correct" : "incorrect"}`;
      status.textContent = `Comparison ${entry.position} / ${COMPARISONS_PER_ROUND} · ${
        entry.answer.correct ? "Correct" : "Incorrect"
      }`;
      const pair = document.createElement("div");
      pair.className = "review-pair";
      const revealed = entry.answer.revealed_anime;
      pair.append(
        makeReviewSide(
          entry.leftAnime,
          revealed.find((anime) => anime.mal_id === entry.leftAnime.mal_id),
          entry,
        ),
        makeReviewSide(
          entry.rightAnime,
          revealed.find((anime) => anime.mal_id === entry.rightAnime.mal_id),
          entry,
        ),
      );
      item.append(status, pair);
      const stat = comparisonStat(entry.category, entry.position);
      const statText = document.createElement("p");
      statText.className = "review-stats";
      statText.textContent = stat
        ? stat.total_answers
          ? `${formatNumber(stat.percentage, 1)}% of players got this right`
          : "No player data yet"
        : "No player data yet";
      item.append(statText);
      section.append(item);
    }
    sections.push(section);
  }
  elements.reviewList.replaceChildren(...sections);
  showScreen(elements.reviewScreen);
  window.scrollTo({ top: 0, behavior: "smooth" });
}

async function submitCompletion() {
  const sequence = state.runSequence;
  state.transitioning = true;
  elements.retryCompletionButton.hidden = true;

  try {
    const challengeDate = encodeURIComponent(state.challenge.challenge_date);
    const response = await fetch(`/challenge/${challengeDate}/complete`, {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ answers: state.selections }),
    });
    const completion = await readJsonResponse(response);
    if (sequence !== state.runSequence) return;
    showResults(completion);
  } catch (error) {
    if (sequence !== state.runSequence) return;
    for (const card of elements.animeCards.children) {
      card.classList.remove("round-exit");
    }

    elements.requestError.textContent = `${
      error.message || "Your completed run could not be verified."
    } Try saving the result again.`;
    elements.requestError.hidden = false;
    elements.retryCompletionButton.hidden = false;
    state.transitioning = false;
  }
}

function archiveDateString(year, month, day) {
  return `${year}-${String(month).padStart(2, "0")}-${String(day).padStart(
    2,
    "0",
  )}`;
}

function showArchivedResult(challenge) {
  state.selectedArchiveDate = challenge.challenge_date;
  elements.challengeLabel.textContent = "Archive challenge";
  elements.challengeDate.textContent = formatDate(challenge.challenge_date);
  elements.archiveResultDate.textContent = formatDate(challenge.challenge_date);
  elements.archiveResultScore.textContent =
    `${challenge.official_score} / ${challenge.total_questions}`;
  elements.archiveResultPercentage.textContent =
    `${formatNumber(challenge.percentage, 2)}% correct`;
  elements.practiceButton.hidden = !challenge.playable;
  elements.archiveResultCopy.textContent = challenge.playable
    ? "This score is locked as your official result. Replays are practice only."
    : "This result belongs to an older five-round challenge. Its score is preserved, but this version cannot be replayed.";
  setActiveNavigation("archive");
  showScreen(elements.archiveResultScreen);
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function renderArchiveCalendar(archive) {
  const challengesByDate = new Map(
    archive.challenges.map((challenge) => [challenge.challenge_date, challenge]),
  );
  const firstWeekday = new Date(archive.year, archive.month - 1, 1).getDay();
  const cells = [];

  elements.archiveMonthTitle.textContent = `${archive.month_name} ${archive.year}`;

  for (let index = 0; index < firstWeekday; index += 1) {
    const emptyCell = document.createElement("div");
    emptyCell.className = "calendar-day is-empty";
    emptyCell.setAttribute("aria-hidden", "true");
    cells.push(emptyCell);
  }

  for (let day = 1; day <= archive.days_in_month; day += 1) {
    const challengeDate = archiveDateString(archive.year, archive.month, day);
    if (!isPublicHistoryDate(challengeDate, archive.public_history_start)) {
      const calendarDate = document.createElement("div");
      calendarDate.className = "calendar-day is-before-public-history";
      const dayNumber = document.createElement("strong");
      dayNumber.textContent = String(day);
      calendarDate.append(dayNumber);
      cells.push(calendarDate);
      continue;
    }
    const challenge = challengesByDate.get(challengeDate);
    const isFuture = challengeDate > archive.today;
    const dayButton = document.createElement("button");
    dayButton.className = "calendar-day";
    dayButton.type = "button";
    dayButton.disabled = !challenge || isFuture ||
      (!challenge.playable && !challenge.completed);

    if (challengeDate === archive.today) {
      dayButton.classList.add("is-today");
    }

    const dayNumber = document.createElement("strong");
    dayNumber.textContent = String(day);
    const dayStatus = document.createElement("span");

    if (challenge && !isFuture) {
      dayButton.classList.add("has-challenge");

      if (challenge.completed) {
        dayButton.classList.add("is-completed");
        dayStatus.textContent = `${challenge.official_score}/${challenge.total_questions}`;
        dayButton.setAttribute(
          "aria-label",
          `${formatDate(challengeDate)}, completed with ${dayStatus.textContent}`,
        );
        dayButton.addEventListener("click", () => showArchivedResult(challenge));
      } else if (!challenge.playable) {
        dayStatus.textContent = "Legacy";
        dayButton.setAttribute(
          "aria-label",
          `${formatDate(challengeDate)}, older challenge not playable`,
        );
      } else {
        dayStatus.textContent = "Available";
        dayButton.setAttribute(
          "aria-label",
          `${formatDate(challengeDate)}, challenge available`,
        );
        dayButton.addEventListener("click", () => loadChallenge(challengeDate));
      }
    } else {
      dayStatus.textContent = isFuture ? "Future" : "—";
    }

    dayButton.append(dayNumber, dayStatus);
    cells.push(dayButton);
  }

  elements.calendarGrid.replaceChildren(...cells);
}

async function loadArchive(year = state.archiveYear, month = state.archiveMonth) {
  cancelPendingFeedback();
  introSequence += 1;
  const requestId = navigationRequests.begin();
  state.runSequence += 1;
  state.archiveYear = year;
  state.archiveMonth = month;
  elements.archiveError.hidden = true;
  elements.calendarGrid.replaceChildren();
  elements.archiveMonthTitle.textContent = "Loading…";
  elements.challengeLabel.textContent = "Challenge archive";
  elements.challengeDate.textContent = new Intl.DateTimeFormat(undefined, {
    month: "long",
    year: "numeric",
  }).format(new Date(year, month - 1, 1));
  setActiveNavigation("archive");
  showScreen(elements.archiveScreen);

  try {
    const response = await fetch(
      `/archive?year=${year}&month=${month}&local_date=${encodeURIComponent(localDateString())}`,
      {
      headers: { Accept: "application/json" },
      },
    );
    const archive = await readJsonResponse(response);
    if (!navigationRequests.isCurrent(requestId)) return;
    state.publicHistoryStart = archive.public_history_start;
    state.archiveData = archive;
    renderArchiveCalendar(archive);
  } catch (error) {
    if (!navigationRequests.isCurrent(requestId)) return;
    elements.archiveMonthTitle.textContent = "Archive unavailable";
    elements.archiveError.textContent =
      error.message || "The challenge archive could not be loaded.";
    elements.archiveError.hidden = false;
  }
}

function changeArchiveMonth(offset) {
  let year = state.archiveYear;
  let month = state.archiveMonth + offset;

  if (month < 1) {
    year -= 1;
    month = 12;
  } else if (month > 12) {
    year += 1;
    month = 1;
  }

  loadArchive(year, month);
}

async function advanceGame() {
  if (!state.answer || state.transitioning) {
    return;
  }

  if (state.comparisonIndex < COMPARISONS_PER_ROUND - 1) {
    await animateToNextComparison();
    return;
  }

  if (!await animateRoundExit()) return;

  if (state.roundIndex < TOTAL_ROUNDS - 1) {
    showRoundComplete();
  } else {
    if (PLAYTEST_MODE) {
      showResults();
    } else {
      await submitCompletion();
    }
  }
}

function resetGame() {
  cancelPendingFeedback();
  state.runSequence += 1;
  state.roundIndex = 0;
  state.comparisonIndex = 0;
  state.totalScore = 0;
  state.roundScore = 0;
  state.answer = null;
  state.revealPhase = null;
  state.waitingForAnswer = false;
  state.transitioning = false;
  state.revealedMetrics.clear();
  state.selections = [];
  state.reviewEntries = [];
  state.completion = null;
  roundContinueGate.reset();
  elements.roundContinueButton.disabled = false;
  elements.reviewList.replaceChildren();
  elements.retryCompletionButton.hidden = true;
}

function replayGame() {
  introSequence += 1;
  resetGame();
  showRoundIntro();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

elements.retryCompletionButton.addEventListener("click", submitCompletion);
elements.reviewButton.addEventListener("click", openReview);
elements.reviewReturnButton.addEventListener("click", () => {
  showScreen(elements.resultsScreen);
  window.scrollTo({ top: 0, behavior: "smooth" });
});
elements.retryLoadButton.addEventListener("click", () =>
  loadChallenge(state.challengeRequestDate, state.retryGeneratePlaytest),
);
elements.replayButton.addEventListener("click", replayGame);
elements.newPlaytestButton.addEventListener("click", () =>
  loadChallenge("playtest", true),
);
elements.landingStartButton.addEventListener("click", startOrResumeTodayChallenge);
elements.roundContinueButton.addEventListener("click", continueToNextRound);
elements.mobileAdDismiss.addEventListener("click", dismissMobileAd);
elements.todayNavButton.addEventListener("click", restoreTodayView);
elements.archiveNavButton.addEventListener("click", () => loadArchive());
elements.previousMonthButton.addEventListener("click", () => changeArchiveMonth(-1));
elements.nextMonthButton.addEventListener("click", () => changeArchiveMonth(1));
elements.practiceButton.addEventListener("click", () =>
  loadChallenge(state.selectedArchiveDate),
);
elements.archiveReturnButton.addEventListener("click", () => loadArchive());
elements.resultsArchiveButton.addEventListener("click", () => loadArchive());
elements.brand.addEventListener("click", (event) => {
  if (
    PLAYTEST_MODE ||
    event.button !== 0 ||
    event.metaKey ||
    event.ctrlKey ||
    event.shiftKey ||
    event.altKey
  ) {
    return;
  }
  event.preventDefault();
  restoreTodayView();
});
window.addEventListener("pageshow", () => {
  resyncOfficialProgress();
});

if (PLAYTEST_MODE) {
  elements.brand.href = "/?playtest=1";
  elements.todayNavButton.textContent = "Playtest";
  elements.archiveNavButton.hidden = true;
  elements.challengeLabel.textContent = "PLAYTEST";
  elements.challengeLabel.classList.add("is-playtest");
}

loadChallenge(PLAYTEST_MODE ? "playtest" : "today");
