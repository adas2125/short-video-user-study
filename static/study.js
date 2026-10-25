/* creating state variables 
player: the YouTube player object.
trial: the currently assigned video/trial metadata from Flask.
probeHasFired: prevents the prompt from appearing twice.
promptStartedAt: browser timestamp for calculating response latency.
poller: stores the repeating timer that checks the current video time.
*/

let player;
let trial;
let nextProbeIndex = 0;
let activeProbe = null;
let probeHasFired = false;
let promptStartedAt = null;
let poller;
let awaitingChoice = false;
let endedWhileAwaitingChoice = false;
// Only allow the end overlay after the participant chose Continue.
let shouldShowEndOverlay = false;

// specific html elements used in the study interface
const title = document.querySelector("#video-title");
const questionOverlay = document.querySelector("#question-overlay");
const endOverlay = document.querySelector("#end-overlay");
const completeCard = document.querySelector("#complete-card");
const status = document.querySelector("#status");

// helper function for making API requests
async function api(url, options = {}) {
  // sends an API request to the given URL with the specified options
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  // parse the JSON response and check for errors
  if (!response.ok) throw new Error(`Request failed: ${response.status}`);
  return response.json();
}

// build the event payload for logging
function eventPayload(eventType, details = {}) {
  return {
    trial_id: trial.trial_id,
    event_type: eventType,
    video_time: player?.getCurrentTime?.() ?? null,
    client_wall_time: new Date().toISOString(),
    details,
  };
}

// log an event to the server
function logEvent(eventType, details) {
  // turns the event type and details into a payload and sends it to the server
  return api("/api/event", {
    method: "POST",
    body: JSON.stringify(eventPayload(eventType, details)),
  }).catch((error) => console.warn("Could not log event", error));
}

// request the current trial from the server and update the interface accordingly
async function loadTrial() {
  const payload = await api("/api/trial/current");

  // if the study is complete, show the completion card and hide the video player
  if (payload.complete) {
    document.querySelector(".player-card").hidden = true;
    title.hidden = true;
    completeCard.hidden = false;
    return;
  }

  // new trial has been loaded, reset state and update interface accordingly
  trial = payload;
  nextProbeIndex = payload.next_probe_index;
  activeProbe = null;
  probeHasFired = false;
  promptStartedAt = null;
  title.textContent = payload.title;
  questionOverlay.hidden = true;
  endOverlay.hidden = true;
  awaitingChoice = false;
  endedWhileAwaitingChoice = false;
  shouldShowEndOverlay = false;

  // Tell the YouTube player to load the assigned Short using its YouTube ID.
  if (player) player.loadVideoById(payload.youtube_id);
}

function showPrompt(probe) {
  if (probeHasFired) return;

  probeHasFired = true;
  activeProbe = probe;
  awaitingChoice = true;
  endedWhileAwaitingChoice = false;
  promptStartedAt = performance.now();
  questionOverlay.hidden = false;
  player.pauseVideo();

  logEvent("probe_shown", {
    probe_index: nextProbeIndex,
    planned_probe_time: activeProbe.time,
    probe_category: activeProbe.category,
  });
}

function checkProbe() {
  // repeatedly checks whether the video reached the next assigned pause moment
  if (!trial || probeHasFired || !player?.getPlayerState) return;

  const nextProbe = trial.probes[nextProbeIndex];

  // There are no more probes in this video.
  if (!nextProbe) return;

  if (
    player.getPlayerState() === YT.PlayerState.PLAYING &&
    player.getCurrentTime() >= nextProbe.time
  ) {
    showPrompt(nextProbe);
  }
}

async function submitChoice(choice) {
  // do nothing if the prompt has not been shown yet
  if (!promptStartedAt) return;

  // disable all choice buttons to prevent multiple submissions
  document.querySelectorAll("[data-choice]").forEach((button) => {
    button.disabled = true;
  });

  // measure response latency
  const latency = Math.round(performance.now() - promptStartedAt);

  // Send the participant’s answer and response time to Flask
  await api("/api/response", {
    method: "POST",
    body: JSON.stringify({
      trial_id: trial.trial_id,
      probe_index: nextProbeIndex,
      choice,
      response_latency_ms: latency,
    }),
  });

  await logEvent("response_submitted", {
    probe_index: nextProbeIndex,
    planned_probe_time: activeProbe.time,
    probe_category: activeProbe.category,
    choice,
    response_latency_ms: latency,
  });

  questionOverlay.hidden = true;
  awaitingChoice = false;

  document.querySelectorAll("[data-choice]").forEach((button) => {
    button.disabled = false;
  });

  // based on the participant's choice, either continue watching the video or move to the next trial
  if (choice === "continue") {
    nextProbeIndex += 1;
    probeHasFired = false;
    activeProbe = null;

    if (endedWhileAwaitingChoice) {
      // The video ended while the question was visible.
      endOverlay.hidden = false;
    } else {
      shouldShowEndOverlay = true;
      player.playVideo();
    }
  } else {
    await nextTrial();
  }
}

async function nextTrial() {
  if (!trial) return;

  // Tell Flask to mark the current trial as ended and increment the participant’s current position
  await api("/api/trial/next", {
    method: "POST",
    body: JSON.stringify({ trial_id: trial.trial_id }),
  });

  // request and load the next trial from the server
  await loadTrial();
}

window.onYouTubeIframeAPIReady = async function onYouTubeIframeAPIReady() {
  player = new YT.Player("player", {
    width: "360",
    height: "640",
    playerVars: {
      playsinline: 1,
      controls: 0,
      disablekb: 1,
      fs: 0,
      rel: 0,
    },
    events: {
      // once the player is ready, load the first trial
      onReady: loadTrial,

      onStateChange: (event) => {
        if (event.data === YT.PlayerState.PLAYING) {
          logEvent("video_started");
        }

        if (event.data === YT.PlayerState.ENDED) {
          logEvent("video_ended");

          if (awaitingChoice) {
            endedWhileAwaitingChoice = true;
          } else if (shouldShowEndOverlay) {
            endOverlay.hidden = false;
          }
        }
      },

      onError: (event) => logEvent("player_error", { code: event.data }),
    },
  });

  // Run checkProbe() every 150 milliseconds
  poller = window.setInterval(checkProbe, 150);
};

// Attach event listeners to choice buttons and the next trial button
document.querySelectorAll("[data-choice]").forEach((button) => {
  button.addEventListener("click", () => {
    submitChoice(button.dataset.choice);
  });
});

document.querySelector("#next-button").addEventListener("click", nextTrial);
