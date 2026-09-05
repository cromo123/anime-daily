const TOTAL_ROUNDS = 5;
const COMPARISONS_PER_ROUND = 5;
const TOTAL_COMPARISONS = TOTAL_ROUNDS * COMPARISONS_PER_ROUND;
const INTRO_DURATION_MS = 1200;
const CARD_TRANSITION_MS = 420;
const CHALLENGER_ENTER_MS = 360;

const state = {
  challenge: null,
  roundIndex: 0,
  comparisonIndex: 0,
  totalScore: 0,
  roundScore: 0,
  answer: null,
  waitingForAnswer: false,
  transitioning: false,
  revealedMetrics: new Map(),
  selections: [],
  completion: null,
  challengeRequestDate: "today",
  playingArchivedChallenge: false,
  archiveYear: new Date().getFullYear(),
  archiveMonth: new Date().getMonth() + 1,
  archiveData: null,
  selectedArchiveDate: null,
};

let introSequence = 0;

const elements = {
  loadingScreen: document.querySelector("#loading-screen"),
  errorScreen: document.querySelector("#error-screen"),
  roundIntro: document.querySelector("#round-intro"),
  gameScreen: document.querySelector("#game-screen"),
  resultsScreen: document.querySelector("#results-screen"),
  archiveScreen: document.querySelector("#archive-screen"),
  archiveResultScreen: document.querySelector("#archive-result-screen"),
  challengeDate: document.querySelector("#challenge-date"),
  challengeLabel: document.querySelector("#challenge-label"),
  todayNavButton: document.querySelector("#today-nav-button"),
  archiveNavButton: document.querySelector("#archive-nav-button"),
  introRound: document.querySelector("#intro-round"),
  introTitle: document.querySelector("#intro-title"),
  introQuestion: document.querySelector("#intro-question"),
  categoryCount: document.querySelector("#category-count"),
  categoryName: document.querySelector("#category-name"),
  categoryQuestion: document.querySelector("#category-question"),
  comparisonLabel: document.querySelector("#comparison-label"),
  roundScoreCount: document.querySelector("#round-score-count"),
  animeCards: document.querySelector("#anime-cards"),
  answerMessage: document.querySelector("#answer-message"),
  answerResult: document.querySelector("#answer-result"),
  answerDetail: document.querySelector("#answer-detail"),
  requestError: document.querySelector("#request-error"),
  nextButton: document.querySelector("#next-button"),
  retryLoadButton: document.querySelector("#retry-load-button"),
  loadErrorMessage: document.querySelector("#load-error-message"),
  finalScore: document.querySelector("#final-score"),
  finalPercentage: document.querySelector("#final-percentage"),
  resultsCopy: document.querySelector("#results-copy"),
  resultsEyebrow: document.querySelector("#results-eyebrow"),
  resultsTitle: document.querySelector("#results-title"),
  replayButton: document.querySelector("#replay-button"),
  resultsArchiveButton: document.querySelector("#results-archive-button"),
  archiveMonthTitle: document.querySelector("#archive-month-title"),
  calendarGrid: document.querySelector("#calendar-grid"),
  archiveError: document.querySelector("#archive-error"),
  previousMonthButton: document.querySelector("#previous-month-button"),
  nextMonthButton: document.querySelector("#next-month-button"),
  archiveResultDate: document.querySelector("#archive-result-date"),
  archiveResultScore: document.querySelector("#archive-result-score"),
  archiveResultPercentage: document.querySelector("#archive-result-percentage"),
  practiceButton: document.querySelector("#practice-button"),
  archiveReturnButton: document.querySelector("#archive-return-button"),
};

function showScreen(screen) {
  for (const candidate of [
    elements.loadingScreen,
    elements.errorScreen,
    elements.roundIntro,
    elements.gameScreen,
    elements.resultsScreen,
    elements.archiveScreen,
    elements.archiveResultScreen,
  ]) {
    candidate.hidden = candidate !== screen;
  }
}

function prefersReducedMotion() {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

function wait(milliseconds) {
  const duration = prefersReducedMotion() ? 0 : milliseconds;
  return new Promise((resolve) => window.setTimeout(resolve, duration));
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

async function readJsonResponse(response) {
  const data = await response.json().catch(() => null);

  if (!response.ok) {
    const message = data?.detail || `Request failed with status ${response.status}.`;
    throw new Error(message);
  }

  return data;
}

async function loadChallenge(challengeDate = "today") {
  introSequence += 1;
  state.challengeRequestDate = challengeDate;
  showScreen(elements.loadingScreen);

  try {
    const challengePath =
      challengeDate === "today"
        ? "/challenge/today"
        : `/challenge/${encodeURIComponent(challengeDate)}`;
    const response = await fetch(challengePath, {
      headers: { Accept: "application/json" },
    });
    const challenge = await readJsonResponse(response);

    if (!validateChallenge(challenge)) {
      throw new Error("The daily challenge data is incomplete.");
    }

    state.challenge = challenge;
    state.playingArchivedChallenge =
      challenge.challenge_date !== localDateString();
    resetGame();
    elements.challengeDate.textContent = formatDate(challenge.challenge_date);
    elements.challengeLabel.textContent = state.playingArchivedChallenge
      ? "Archive challenge"
      : "Daily challenge";
    setActiveNavigation(state.playingArchivedChallenge ? "archive" : "today");
    showRoundIntro();
  } catch (error) {
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
    return `Released: ${formatDate(revealedAnime.release_date)}`;
  }

  if (categoryName === "Longer Runtime") {
    return `Runtime: ${formatNumber(revealedAnime.runtime_minutes)} minutes`;
  }

  return "";
}

function addMetricReveal(cardCopy, revealedAnime) {
  const metric = document.createElement("p");
  metric.className = "metric-reveal";
  metric.textContent = formatReveal(currentRound().name, revealedAnime);
  cardCopy.append(metric);
}

function addCardVerdict(card, label, carried = false) {
  const verdict = document.createElement("span");
  verdict.className = carried
    ? "card-verdict carry-verdict"
    : "card-verdict";
  verdict.textContent = label;
  card.append(verdict);
}

function createAnimeCard(anime, choiceNumber, entering = false) {
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

  const number = document.createElement("span");
  number.className = "choice-number";
  number.textContent = String(choiceNumber);

  const title = document.createElement("h2");
  title.className = "anime-title";
  title.textContent = anime.title;

  const chooseLabel = document.createElement("p");
  chooseLabel.className = "choose-label";
  chooseLabel.textContent = "Choose this anime";

  cardCopy.append(number, title, chooseLabel);
  card.append(createCover(anime), cardCopy);

  if (state.answer) {
    const revealedAnime = state.answer.revealed_anime.find(
      (revealed) => revealed.mal_id === anime.mal_id,
    );
    const isCorrectAnime = anime.mal_id === state.answer.correct_mal_id;
    const wasSelected = anime.mal_id === state.answer.selected_mal_id;

    if (isCorrectAnime) {
      card.classList.add("is-correct");
      addCardVerdict(card, "Correct");
    } else if (wasSelected) {
      card.classList.add("is-incorrect");
    } else {
      card.classList.add("is-dimmed");
    }

    chooseLabel.textContent = wasSelected ? "Your choice" : "Other choice";
    addMetricReveal(cardCopy, revealedAnime);
  } else {
    const carriedReveal = revealedMetricFor(anime);

    if (carriedReveal) {
      card.classList.add("is-carried");
      chooseLabel.textContent = "Carries forward";
      addCardVerdict(card, "Carries forward", true);
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

function updateNextButton() {
  const isLastComparison =
    state.comparisonIndex === COMPARISONS_PER_ROUND - 1;
  const isLastRound = state.roundIndex === TOTAL_ROUNDS - 1;

  if (isLastComparison && isLastRound) {
    elements.nextButton.textContent = "See final score";
  } else if (isLastComparison) {
    elements.nextButton.textContent = "Next round";
  } else {
    elements.nextButton.textContent = "Next comparison";
  }
}

function renderComparison() {
  const animePair = currentAnimePair();

  updateRoundStatus();
  elements.requestError.hidden = true;
  elements.animeCards.replaceChildren(
    createAnimeCard(animePair[0], 1),
    createAnimeCard(animePair[1], 2),
  );

  if (state.answer) {
    elements.nextButton.disabled = state.transitioning;
    elements.answerResult.textContent = state.answer.correct
      ? "Correct choice"
      : "Not quite";
    elements.answerResult.className = `answer-result ${
      state.answer.correct ? "correct" : "incorrect"
    }`;
    elements.answerDetail.textContent = state.answer.correct
      ? "Your round score has been updated."
      : "The correct anime is highlighted above.";
    elements.answerMessage.hidden = false;
    updateNextButton();
  } else {
    elements.answerMessage.hidden = true;
  }
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
  setChoicesDisabled(true);
  elements.requestError.hidden = true;

  try {
    const challengeDate = encodeURIComponent(state.challenge.challenge_date);
    const response = await fetch(`/challenge/${challengeDate}/answer`, {
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

    rememberRevealedMetrics(answer);
    state.answer = answer;
    state.selections.push({
      category: currentRound().name,
      comparison_position: state.comparisonIndex + 1,
      selected_mal_id: selectedMalId,
    });

    if (answer.correct) {
      state.totalScore += 1;
      state.roundScore += 1;
    }

    renderComparison();
  } catch (error) {
    elements.requestError.textContent = `${
      error.message || "The answer could not be checked."
    } Please try again.`;
    elements.requestError.hidden = false;
    setChoicesDisabled(false);
  } finally {
    state.waitingForAnswer = false;
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
  card.querySelector(".choice-number").textContent = "1";
  card.querySelector(".choose-label").textContent = "Carries forward";
  addCardVerdict(card, "Carries forward", true);
  card.addEventListener("click", () => submitAnswer(anime.mal_id));
}

async function animateToNextComparison() {
  state.transitioning = true;
  elements.nextButton.disabled = true;

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

  leftCard.remove();
  state.comparisonIndex += 1;
  state.answer = null;
  elements.answerMessage.hidden = true;
  elements.requestError.hidden = true;

  const [carriedAnime, incomingAnime] = currentAnimePair();
  prepareCarriedCard(rightCard, carriedAnime);
  const incomingCard = createAnimeCard(incomingAnime, 2, true);
  elements.animeCards.append(incomingCard);
  updateRoundStatus();

  await wait(CHALLENGER_ENTER_MS);

  incomingCard.classList.remove("chain-enter-right");
  state.transitioning = false;
  elements.nextButton.disabled = false;
  setChoicesDisabled(false);
}

async function animateRoundExit() {
  state.transitioning = true;
  elements.nextButton.disabled = true;

  for (const card of elements.animeCards.children) {
    card.classList.add("round-exit");
  }

  await wait(CHALLENGER_ENTER_MS);
}

function showResults(completion) {
  elements.finalScore.textContent = `${completion.verified_score} / ${completion.total_questions}`;
  elements.finalPercentage.textContent = `${formatNumber(
    completion.verified_percentage,
    2,
  )}% correct`;

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
    : "That’s today’s 25.";
  elements.replayButton.textContent = state.playingArchivedChallenge
    ? "Replay as practice"
    : "Replay today’s challenge";
  elements.resultsArchiveButton.hidden = !state.playingArchivedChallenge;

  state.completion = completion;
  state.transitioning = false;
  showScreen(elements.resultsScreen);
  window.scrollTo({ top: 0, behavior: "smooth" });
}

async function submitCompletion() {
  elements.nextButton.textContent = "Saving result…";
  elements.nextButton.disabled = true;

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
    showResults(completion);
  } catch (error) {
    for (const card of elements.animeCards.children) {
      card.classList.remove("round-exit");
    }

    elements.requestError.textContent = `${
      error.message || "Your completed run could not be verified."
    } Press the button to try saving the result again.`;
    elements.requestError.hidden = false;
    elements.nextButton.textContent = "Retry final result";
    elements.nextButton.disabled = false;
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
    const challenge = challengesByDate.get(challengeDate);
    const isFuture = challengeDate > archive.today;
    const dayButton = document.createElement("button");
    dayButton.className = "calendar-day";
    dayButton.type = "button";
    dayButton.disabled = !challenge || isFuture;

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
  introSequence += 1;
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
    const response = await fetch(`/archive?year=${year}&month=${month}`, {
      headers: { Accept: "application/json" },
    });
    const archive = await readJsonResponse(response);
    state.archiveData = archive;
    renderArchiveCalendar(archive);
  } catch (error) {
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

  await animateRoundExit();

  if (state.roundIndex < TOTAL_ROUNDS - 1) {
    state.roundIndex += 1;
    state.comparisonIndex = 0;
    state.roundScore = 0;
    state.answer = null;
    state.transitioning = false;
    elements.answerMessage.hidden = true;
    showRoundIntro();
  } else {
    await submitCompletion();
  }
}

function resetGame() {
  state.roundIndex = 0;
  state.comparisonIndex = 0;
  state.totalScore = 0;
  state.roundScore = 0;
  state.answer = null;
  state.waitingForAnswer = false;
  state.transitioning = false;
  state.revealedMetrics.clear();
  state.selections = [];
  state.completion = null;
}

function replayGame() {
  resetGame();
  showRoundIntro();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

elements.nextButton.addEventListener("click", advanceGame);
elements.retryLoadButton.addEventListener("click", () =>
  loadChallenge(state.challengeRequestDate),
);
elements.replayButton.addEventListener("click", replayGame);
elements.todayNavButton.addEventListener("click", () => loadChallenge("today"));
elements.archiveNavButton.addEventListener("click", () => loadArchive());
elements.previousMonthButton.addEventListener("click", () => changeArchiveMonth(-1));
elements.nextMonthButton.addEventListener("click", () => changeArchiveMonth(1));
elements.practiceButton.addEventListener("click", () =>
  loadChallenge(state.selectedArchiveDate),
);
elements.archiveReturnButton.addEventListener("click", () => loadArchive());
elements.resultsArchiveButton.addEventListener("click", () => loadArchive());

loadChallenge();
