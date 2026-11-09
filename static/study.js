// DOM elements for the video player, overlays, and controls
const player = document.querySelector("#player");
const title = document.querySelector("#video-title");
const startOverlay = document.querySelector("#start-overlay");
const startButton = document.querySelector("#start-button");
const questionOverlay = document.querySelector("#question-overlay");
const endOverlay = document.querySelector("#end-overlay");
const freeSwipeControls = document.querySelector("#free-swipe-controls");
const swipeButton = document.querySelector("#swipe-button");
const completeCard = document.querySelector("#complete-card");
const status = document.querySelector("#status");

let trial;
let nextProbeIndex = 0;
let activeProbe = null;
let probeHasFired = false;
let awaitingChoice = false;
let endedWhileAwaitingChoice = false;
let shouldAdvanceAfterVideoEnds = false;
let advancingTrial = false;
let hasStartedStudy = false;
let autoplayWhenReady = false;
let retryingVideo = false;
let currentTrialAlreadyAdvanced = false;

// Helper function to make API requests to the backend study endpoints
async function api(url, options = {}) {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) throw new Error(`Request failed: ${response.status}`);
  return response.json();
}

function eventPayload(eventType, details = {}) {
  return {
    trial_id: trial.trial_id,
    event_type: eventType,
    video_time: Number.isFinite(player.currentTime) ? player.currentTime : null,
    client_wall_time: new Date().toISOString(),
    details,
  };
}

function logEvent(eventType, details = {}) {
  return api("/api/event", {
    method: "POST",
    body: JSON.stringify(eventPayload(eventType, details)),
  }).catch((error) => console.warn("Could not log event", error));
}

function resetTrialInterface() {
  activeProbe = null;
  probeHasFired = false;
  awaitingChoice = false;
  endedWhileAwaitingChoice = false;
  shouldAdvanceAfterVideoEnds = false;
  questionOverlay.hidden = true;
  endOverlay.hidden = true;
  freeSwipeControls.hidden = true;
  status.textContent = "Loading video…";
}

function isSwipeUnlocked() {
  // allow swipes if free swipe or past the probe
  return trial?.interaction_mode === "free_swipe" || (
    trial?.interaction_mode === "fixed_probe" &&
    trial.probes.length === 1 &&
    trial.last_probe_choice === "continue" &&
    nextProbeIndex === 1
  );
}

function updateInteractionControls() {
  // Show or hide the free swipe controls based on the current trial and player state
  freeSwipeControls.hidden = (
    !isSwipeUnlocked() ||
    !hasStartedStudy ||
    player.paused ||
    player.ended
  );
}

async function loadTrial({ autoplay = false } = {}) {
  // Fetch the current trial for the participant from the backend
  const payload = await api("/api/trial/current");

  // if participant has completed all trials
  if (payload.complete) {
    player.pause();
    player.removeAttribute("src");
    player.load();
    document.querySelector(".player-card").hidden = true;
    title.hidden = true;
    completeCard.hidden = false;
    status.textContent = "";
    return;
  }

  // Otherwise, set up the trial for playback
  trial = payload;
  nextProbeIndex = payload.next_probe_index;
  autoplayWhenReady = autoplay;
  retryingVideo = false;
  title.textContent = payload.title;
  resetTrialInterface();

  shouldAdvanceAfterVideoEnds = payload.last_probe_choice === "continue";

  if (payload.last_probe_choice === "next") {
    autoplayWhenReady = false;
    player.pause();
    startOverlay.hidden = true;
    endOverlay.hidden = false;
    status.textContent = "Press Next video to continue.";
    return;
  }

  // The first video needs a deliberate click because browsers block audio autoplay.
  // Later videos try to start automatically; a button appears only if the browser blocks it.
  startOverlay.hidden = hasStartedStudy;
  startButton.disabled = true;
  startButton.textContent = "Loading video…";

  player.pause();
  player.src = payload.video_url;
  player.load();
}

async function startPlayback() {
  try {
    await player.play();
    hasStartedStudy = true;
    startOverlay.hidden = true;
    updateInteractionControls();
    status.textContent = "";
  } catch (error) {
    // A participant can recover with one click if their browser blocks autoplay.
    startOverlay.hidden = false;
    startButton.disabled = false;
    startButton.textContent = "Continue video";
    status.textContent = "Press Continue video to start playback.";
    console.warn("Video playback was blocked", error);
  }
}

function showPrompt(probe) {
  if (probeHasFired) return;

  probeHasFired = true;
  activeProbe = probe;
  awaitingChoice = true;
  endedWhileAwaitingChoice = false;
  // Display the question overlay and pause the video for the participant to respond
  questionOverlay.hidden = false;
  player.pause();

  // Log the event that the probe has been shown to the participant
  logEvent("probe_shown", {
    probe_index: nextProbeIndex,
    planned_probe_time: activeProbe.time,
    probe_category: activeProbe.category,
  });
}

function checkProbe() {
  if (
    !trial ||
    trial.interaction_mode !== "fixed_probe" ||
    probeHasFired ||
    player.paused ||
    player.ended
  ) return;

  const nextProbe = trial.probes[nextProbeIndex];
  // pause the video and show the prompt if it's time for the next probe
  if (nextProbe && player.currentTime >= nextProbe.time) showPrompt(nextProbe);
}

function setChoiceButtonsDisabled(disabled) {
  document.querySelectorAll("[data-choice]").forEach((button) => {
    button.disabled = disabled;
  });
}

async function submitChoice(choice) {
  if (!activeProbe) return;

  // Disable the choice buttons to prevent multiple submissions
  setChoiceButtonsDisabled(true);

  //send to the backend API to record the participant's response for the current probe
  try {
    const savedResponse =await api("/api/response", {
      method: "POST",
      body: JSON.stringify({
        trial_id: trial.trial_id,
        probe_index: nextProbeIndex,
        choice,
      }),
    });
    choice = savedResponse.choice;
    trial.last_probe_choice = choice;
  } catch (error) {
    setChoiceButtonsDisabled(false);
    status.textContent = "Your answer could not be saved. Please try again.";
    console.warn("Could not save response", error);
    return;
  }

  await logEvent("response_submitted", {
    probe_index: nextProbeIndex,
    planned_probe_time: activeProbe.time,
    probe_category: activeProbe.category,
    choice,
  });

  questionOverlay.hidden = true;
  awaitingChoice = false;
  setChoiceButtonsDisabled(false);
  status.textContent = "";

  // handle the continue choice
  if (choice === "continue") {
    // prepare for next probe
    nextProbeIndex += 1;
    probeHasFired = false;
    activeProbe = null;

    if (endedWhileAwaitingChoice) {
      await nextTrial();
    } else {
      // continue with the current video playback
      shouldAdvanceAfterVideoEnds = true;
      await startPlayback();
    }
  } else {
    // choice is not "continue", so advance to the next trial immediately
    await nextTrial();
  }
}

function canVoluntarilySwipe() {
  return (
    isSwipeUnlocked() &&
    hasStartedStudy &&
    !player.paused &&
    !player.ended &&
    !advancingTrial
  );
}

async function voluntarilySwipeToNext(source) {
  if (!canVoluntarilySwipe()) return;

  player.pause();
  try {
    await api("/api/event", {
      method: "POST",
      body: JSON.stringify(eventPayload("voluntary_swipe", { source })),
    });
    await nextTrial();
  } catch (error) {
    status.textContent = "Could not save your swipe. Please try again.";
    console.warn("Could not save voluntary swipe", error);
    await startPlayback();
  }
}

// logic for getting and advancing to the next trial (video)
async function nextTrial() {
  if (!trial || advancingTrial) return;

  advancingTrial = true;
  try {
    // If loading the next MP4 fails after this request succeeds, retain this
    // flag so the Retry button only reloads the same next trial; it never skips one.
    if (!currentTrialAlreadyAdvanced) {
      await api("/api/trial/next", {
        method: "POST",
        body: JSON.stringify({ trial_id: trial.trial_id }),
      });
      currentTrialAlreadyAdvanced = true;
    }

    // Load the next trial's video for playback
    await loadTrial({ autoplay: true });
    currentTrialAlreadyAdvanced = false;
  } catch (error) {
    endOverlay.hidden = false;
    status.textContent = "Could not load the next video. Press Next video to retry.";
    console.warn("Could not advance trial", error);
  } finally {
    advancingTrial = false;
  }
}

player.addEventListener("loadedmetadata", () => {
  if (trial?.last_probe_choice !== "continue") return;

  const answeredProbe = trial.probes[nextProbeIndex - 1];
  if (answeredProbe) player.currentTime = answeredProbe.time;
});

// Event listener for when the video player can start playback
player.addEventListener("canplay", () => {
  retryingVideo = false;
  startButton.disabled = false;
  startButton.textContent = hasStartedStudy ? "Continue video" : "Start study";
  updateInteractionControls();
  status.textContent = hasStartedStudy ? "" : "Video ready.";

  if (autoplayWhenReady) {
    autoplayWhenReady = false;
    startPlayback();
  }
});

// Event listener for when the video starts playing
player.addEventListener("play", () => {
  updateInteractionControls();
  if (trial) logEvent("video_started");
});

// Event listener for when the video ends
player.addEventListener("ended", () => {
  logEvent("video_ended");

  // typically advance to the next trial if the participant has completed the current one
  if (isSwipeUnlocked()) {
    nextTrial();
  } else if (awaitingChoice) {
    endedWhileAwaitingChoice = true;
  } else if (shouldAdvanceAfterVideoEnds) {
    nextTrial();
  }
});

player.addEventListener("error", () => {
  const errorCode = player.error?.code ?? "unknown";
  logEvent("player_error", { code: errorCode });
  retryingVideo = true;
  startOverlay.hidden = false;
  startButton.disabled = false;
  startButton.textContent = "Retry video";
  status.textContent = "This video could not load. Press Retry video.";
});

// Prevent keys commonly used to seek in browser video players.
document.addEventListener("keydown", (event) => {
  if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {
    event.preventDefault();
  }
});

// Event listener for when the participant clicks the start button
startButton.addEventListener("click", () => {
  if (retryingVideo) {
    retryingVideo = false;
    startButton.disabled = true;
    startButton.textContent = "Loading video…";
    status.textContent = "Loading video…";
    player.load();
    return;
  }
  startPlayback();
});

document.querySelectorAll("[data-choice]").forEach((button) => {
  button.addEventListener("click", () => submitChoice(button.dataset.choice));
});

document.querySelector("#next-button").addEventListener("click", nextTrial);
swipeButton.addEventListener("click", () => voluntarilySwipeToNext("next_button"));

// Check the MP4 position frequently enough to pause close to each planned probe.
window.setInterval(checkProbe, 150);

// Load the first MP4 immediately so it is ready by the time the participant presses Start.
loadTrial().catch((error) => {
  status.textContent = "The study could not start. Please refresh the page.";
  console.error("Could not load first trial", error);
});
