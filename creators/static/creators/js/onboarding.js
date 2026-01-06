(() => {
  const dataElement = document.getElementById("creator-onboarding-data");
  if (!dataElement) {
    return;
  }

  let state = JSON.parse(dataElement.textContent);
  const checklist = document.getElementById("onboardingChecklist");
  const modalElement = document.getElementById("onboardingStepModal");
  const modal = modalElement ? new bootstrap.Modal(modalElement) : null;
  const percentLabel = document.getElementById("onboardingPercentLabel");
  const progressBar = document.getElementById("onboardingProgressBar");
  const stepStatusElements = document.querySelectorAll("[data-step-status]");
  const panels = document.querySelectorAll("[data-step-panel]");
  const errorBox = modalElement?.querySelector("[data-onboarding-error]");
  const saveButton = modalElement?.querySelector("[data-onboarding-save]");
  const skipButton = modalElement?.querySelector("[data-onboarding-skip]");
  const performanceMessage = modalElement?.querySelector("[data-performance-message]");
  const addPlatformButton = modalElement?.querySelector("[data-onboarding-add-platform]");
  const platformEntries = modalElement?.querySelector("#platformEntries");

  const stepOrder = ["identity", "platforms", "content", "performance", "payouts"];
  const saveTimers = {};

  const getCsrfToken = () => {
    const match = document.cookie.match(/csrftoken=([^;]+)/);
    return match ? match[1] : "";
  };

  const renderStepStatus = () => {
    stepStatusElements.forEach((el) => {
      const step = el.dataset.stepStatus;
      const isComplete = Boolean(state.steps?.[step]);
      el.classList.remove("completed", "pending");
      el.classList.add(isComplete ? "completed" : "pending");
      el.innerHTML = isComplete ? '<i class="bi bi-check-lg"></i>' : '<i class="bi bi-circle"></i>';
    });
    if (percentLabel) {
      percentLabel.textContent = `${state.completion_percent}% complete`;
    }
    if (progressBar) {
      progressBar.style.width = `${state.completion_percent}%`;
      progressBar.setAttribute("aria-valuenow", state.completion_percent);
    }
    if (state.onboarding_completed && checklist) {
      checklist.classList.add("d-none");
    }
  };

  const updateState = (nextState) => {
    state = { ...state, ...nextState };
    renderStepStatus();
  };

  const showPanel = (step) => {
    panels.forEach((panel) => {
      panel.classList.toggle("d-none", panel.dataset.stepPanel !== step);
    });
    if (errorBox) {
      errorBox.classList.add("d-none");
    }
    if (skipButton) {
      skipButton.classList.toggle("d-none", step === "identity" || step === "platforms" || step === "payouts");
    }
    if (saveButton) {
      saveButton.textContent = step === "payouts" ? "Finish setup" : "Save & continue";
    }
    if (performanceMessage && step === "performance") {
      performanceMessage.textContent = state.performance_message || "We'll add this automatically after your first sale.";
    }
  };

  const openStep = (step) => {
    showPanel(step);
    hydrateFields(step);
    modal?.show();
  };

  const hydrateFields = (step) => {
    const profile = state.profile || {};
    if (step === "identity") {
      modalElement.querySelector("[name='display_name']").value = profile.display_name || "";
      modalElement.querySelector("[name='country']").value = profile.country || "";
      modalElement.querySelector("[name='primary_niches']").value = (profile.primary_niches || []).join(", ");
    }
    if (step === "platforms") {
      renderPlatformEntries(profile.platforms || []);
    }
    if (step === "content") {
      modalElement.querySelector("[name='content_style_tags']").value = (profile.content_style_tags || []).join(", ");
      modalElement.querySelector("[name='posting_frequency']").value = profile.posting_frequency || "";
      modalElement.querySelector("[name='open_to_gifting']").checked = Boolean(profile.open_to_gifting);
    }
    if (step === "payouts") {
      modalElement.querySelector("[name='payout_method']").value = profile.payout_method || "";
      modalElement.querySelector("[name='paypal_email']").value = profile.paypal_email || "";
      modalElement.querySelector("[name='tax_info_submitted']").checked = Boolean(profile.tax_info_submitted);
    }
  };

  const renderPlatformEntries = (platforms) => {
    if (!platformEntries) {
      return;
    }
    platformEntries.innerHTML = "";
    if (!platforms.length) {
      platforms = [{}];
    }
    platforms.forEach((platform) => {
      const entry = document.createElement("div");
      entry.className = "border rounded p-3";
      entry.innerHTML = `
        <div class="row g-2 align-items-end">
          <div class="col-md-4">
            <label class="form-label">Platform</label>
            <select class="form-select" data-platform-field="type">
              <option value="">Select</option>
              <option>TikTok</option>
              <option>Instagram</option>
              <option>YouTube</option>
              <option>Twitch</option>
              <option>X</option>
              <option>Facebook</option>
              <option>Pinterest</option>
            </select>
          </div>
          <div class="col-md-5">
            <label class="form-label">Profile URL</label>
            <input type="url" class="form-control" data-platform-field="url" placeholder="https://..." />
          </div>
          <div class="col-md-3">
            <label class="form-label">Follower range</label>
            <select class="form-select" data-platform-field="followers">
              <option value="">Select</option>
              <option>0-5k</option>
              <option>5k-25k</option>
              <option>25k-100k</option>
              <option>100k+</option>
            </select>
          </div>
        </div>
      `;
      const typeSelect = entry.querySelector("[data-platform-field='type']");
      const urlInput = entry.querySelector("[data-platform-field='url']");
      const followerSelect = entry.querySelector("[data-platform-field='followers']");
      typeSelect.value = platform.type || "";
      urlInput.value = platform.url || "";
      followerSelect.value = platform.followers || "";
      urlInput.addEventListener("blur", () => {
        const detected = detectPlatform(urlInput.value);
        if (detected && !typeSelect.value) {
          typeSelect.value = detected;
        }
        scheduleAutoSave("platforms");
      });
      typeSelect.addEventListener("change", () => scheduleAutoSave("platforms"));
      urlInput.addEventListener("change", () => scheduleAutoSave("platforms"));
      followerSelect.addEventListener("change", () => scheduleAutoSave("platforms"));
      platformEntries.appendChild(entry);
    });
  };

  const detectPlatform = (url) => {
    if (!url) return "";
    const lowered = url.toLowerCase();
    if (lowered.includes("tiktok")) return "TikTok";
    if (lowered.includes("instagram")) return "Instagram";
    if (lowered.includes("youtube") || lowered.includes("youtu.be")) return "YouTube";
    if (lowered.includes("twitch")) return "Twitch";
    if (lowered.includes("facebook")) return "Facebook";
    if (lowered.includes("twitter") || lowered.includes("x.com")) return "X";
    if (lowered.includes("pinterest")) return "Pinterest";
    return "";
  };

  const collectPayload = (step) => {
    const payload = {};
    if (step === "identity") {
      payload.display_name = modalElement.querySelector("[name='display_name']").value.trim();
      payload.country = modalElement.querySelector("[name='country']").value.trim();
      payload.primary_niches = modalElement
        .querySelector("[name='primary_niches']")
        .value.split(",")
        .map((item) => item.trim())
        .filter(Boolean);
    }
    if (step === "platforms") {
      const entries = [];
      platformEntries?.querySelectorAll("[data-platform-field]").forEach(() => {});
      platformEntries?.querySelectorAll(".border").forEach((entry) => {
        const type = entry.querySelector("[data-platform-field='type']").value.trim();
        const url = entry.querySelector("[data-platform-field='url']").value.trim();
        const followers = entry.querySelector("[data-platform-field='followers']").value.trim();
        if (url) {
          entries.push({ type, url, followers });
        }
      });
      payload.platforms = entries;
    }
    if (step === "content") {
      payload.content_style_tags = modalElement
        .querySelector("[name='content_style_tags']")
        .value.split(",")
        .map((item) => item.trim())
        .filter(Boolean);
      payload.posting_frequency = modalElement.querySelector("[name='posting_frequency']").value;
      payload.open_to_gifting = modalElement.querySelector("[name='open_to_gifting']").checked;
    }
    if (step === "performance") {
      payload.skip = true;
    }
    if (step === "payouts") {
      payload.payout_method = modalElement.querySelector("[name='payout_method']").value;
      payload.paypal_email = modalElement.querySelector("[name='paypal_email']").value.trim();
      payload.tax_info_submitted = modalElement.querySelector("[name='tax_info_submitted']").checked;
    }
    return payload;
  };

  const saveStep = async (step, advance = true, skip = false) => {
    const payload = skip ? { skip: true } : collectPayload(step);
    try {
      const response = await fetch(`/api/creator/onboarding/${step}/`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": getCsrfToken(),
        },
        body: JSON.stringify(payload),
      });
      if (!response.ok) {
        throw new Error("Unable to save this step.");
      }
      const data = await response.json();
      updateState(data);
      if (advance) {
        const nextStep = data.next_recommended_step;
        if (nextStep && nextStep !== "complete") {
          openStep(nextStep);
        } else {
          modal?.hide();
        }
      }
    } catch (error) {
      if (errorBox) {
        errorBox.textContent = error.message;
        errorBox.classList.remove("d-none");
      }
    }
  };

  const scheduleAutoSave = (step) => {
    window.clearTimeout(saveTimers[step]);
    saveTimers[step] = window.setTimeout(() => {
      saveStep(step, false, false);
    }, 600);
  };

  const bindAutoSaveFields = () => {
    modalElement.querySelectorAll("[data-onboarding-field]").forEach((field) => {
      field.addEventListener("change", () => {
        const activePanel = modalElement.querySelector("[data-step-panel]:not(.d-none)");
        if (!activePanel) return;
        scheduleAutoSave(activePanel.dataset.stepPanel);
      });
    });
  };

  document.querySelectorAll("[data-onboarding-open]").forEach((button) => {
    button.addEventListener("click", () => {
      openStep(button.dataset.onboardingOpen);
    });
  });

  saveButton?.addEventListener("click", () => {
    const activePanel = modalElement.querySelector("[data-step-panel]:not(.d-none)");
    if (!activePanel) return;
    const step = activePanel.dataset.stepPanel;
    saveStep(step, true, false);
  });

  skipButton?.addEventListener("click", () => {
    const activePanel = modalElement.querySelector("[data-step-panel]:not(.d-none)");
    if (!activePanel) return;
    const step = activePanel.dataset.stepPanel;
    if (step === "content" || step === "performance") {
      saveStep(step, true, true);
    }
  });

  addPlatformButton?.addEventListener("click", () => {
    renderPlatformEntries([
      ...(state.profile?.platforms || []),
      {},
    ]);
  });

  renderStepStatus();
  bindAutoSaveFields();
})();
