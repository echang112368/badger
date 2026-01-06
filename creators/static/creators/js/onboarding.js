(() => {
  const dataEl = document.getElementById("creator-onboarding-data");
  if (!dataEl) {
    return;
  }

  const onboarding = JSON.parse(dataEl.textContent);
  const profile = onboarding.profile || {};

  const progressBar = document.getElementById("onboarding-progress-bar");
  const progressLabel = document.getElementById("onboarding-progress-label");
  const checklist = document.getElementById("onboarding-checklist");
  const successBanner = document.getElementById("onboarding-success");
  const onboardingSection = document.getElementById("creator-onboarding");
  const performanceMessage = document.getElementById("performance-message");
  const platformList = document.querySelector("[data-platform-list]");
  const stepsPanels = document.querySelectorAll("[data-step-panel]");
  const saveButtons = document.querySelectorAll("[data-action='save']");
  const skipButtons = document.querySelectorAll("[data-action='skip']");
  const addPlatformButton = document.querySelector("[data-action='add-platform']");

  if (!checklist) {
    return;
  }

  const saveTimeouts = {};

  const platformOptions = [
    { value: "tiktok", label: "TikTok" },
    { value: "instagram", label: "Instagram" },
    { value: "youtube", label: "YouTube" },
    { value: "pinterest", label: "Pinterest" },
    { value: "blog", label: "Blog" },
    { value: "other", label: "Other" },
  ];

  function getCookie(name) {
    const cookies = document.cookie ? document.cookie.split(";") : [];
    for (let cookie of cookies) {
      cookie = cookie.trim();
      if (cookie.startsWith(`${name}=`)) {
        return decodeURIComponent(cookie.substring(name.length + 1));
      }
    }
    return "";
  }

  function updateProgress(status) {
    const percent = status.completion_percent || 0;
    if (progressBar) {
      progressBar.style.width = `${percent}%`;
      progressBar.setAttribute("aria-valuenow", String(percent));
    }
    if (progressLabel) {
      progressLabel.textContent = `${percent}%`;
    }
  }

  function updateChecklist(status) {
    if (!checklist) {
      return;
    }
    const stepMap = new Map(status.steps.map((step) => [step.step, step]));
    checklist.querySelectorAll("[data-step]").forEach((item) => {
      const step = item.getAttribute("data-step");
      const info = stepMap.get(step);
      const statusEl = item.querySelector("[data-status]");
      if (!info || !statusEl) {
        return;
      }
      if (info.completed) {
        statusEl.textContent = "Done";
      } else if (info.skipped) {
        statusEl.textContent = "Skipped";
      } else if (info.required) {
        statusEl.textContent = "Required";
      } else {
        statusEl.textContent = "Optional";
      }
    });
  }

  function showStep(step) {
    stepsPanels.forEach((panel) => {
      if (panel.getAttribute("data-step-panel") === step) {
        panel.classList.remove("d-none");
      } else {
        panel.classList.add("d-none");
      }
    });
  }

  function setProfileFields() {
    document.querySelectorAll("[data-field]").forEach((input) => {
      const field = input.getAttribute("data-field");
      if (!field) {
        return;
      }
      if (field === "open_to_gifting") {
        input.checked = Boolean(profile.open_to_gifting);
        return;
      }
      if (field === "primary_niches") {
        input.value = (profile.primary_niches || []).join(", ");
        return;
      }
      if (field === "content_style_tags") {
        input.value = (profile.content_style_tags || []).join(", ");
        return;
      }
      if (profile[field] !== undefined && profile[field] !== null) {
        input.value = profile[field];
      }
    });
  }

  function detectPlatform(url) {
    const lower = url.toLowerCase();
    if (lower.includes("tiktok.com")) return "tiktok";
    if (lower.includes("instagram.com")) return "instagram";
    if (lower.includes("youtube.com") || lower.includes("youtu.be")) return "youtube";
    if (lower.includes("pinterest.com")) return "pinterest";
    if (lower.includes("blog") || lower.includes("medium.com")) return "blog";
    return "other";
  }

  function buildPlatformRow(entry = {}) {
    const wrapper = document.createElement("div");
    wrapper.className = "card border-0 shadow-sm mb-2 p-3";
    wrapper.setAttribute("data-platform-entry", "true");

    const row = document.createElement("div");
    row.className = "row g-2 align-items-end";

    const platformCol = document.createElement("div");
    platformCol.className = "col-md-4";
    const platformLabel = document.createElement("label");
    platformLabel.className = "form-label";
    platformLabel.textContent = "Platform";
    const platformSelect = document.createElement("select");
    platformSelect.className = "form-select";
    platformSelect.setAttribute("data-platform-type", "true");
    platformOptions.forEach((option) => {
      const opt = document.createElement("option");
      opt.value = option.value;
      opt.textContent = option.label;
      platformSelect.appendChild(opt);
    });
    platformSelect.value = entry.platform || "";

    platformCol.appendChild(platformLabel);
    platformCol.appendChild(platformSelect);

    const urlCol = document.createElement("div");
    urlCol.className = "col-md-5";
    const urlLabel = document.createElement("label");
    urlLabel.className = "form-label";
    urlLabel.textContent = "Profile URL";
    const urlInput = document.createElement("input");
    urlInput.className = "form-control";
    urlInput.type = "url";
    urlInput.placeholder = "https://";
    urlInput.setAttribute("data-platform-url", "true");
    urlInput.value = entry.url || "";
    urlInput.addEventListener("blur", () => {
      if (urlInput.value && !platformSelect.value) {
        platformSelect.value = detectPlatform(urlInput.value);
      }
    });

    urlCol.appendChild(urlLabel);
    urlCol.appendChild(urlInput);

    const followerCol = document.createElement("div");
    followerCol.className = "col-md-2";
    const followerLabel = document.createElement("label");
    followerLabel.className = "form-label";
    followerLabel.textContent = "Followers";
    const followerInput = document.createElement("input");
    followerInput.className = "form-control";
    followerInput.type = "text";
    followerInput.placeholder = "10k-50k";
    followerInput.setAttribute("data-platform-followers", "true");
    followerInput.value = entry.followers_range || "";
    followerCol.appendChild(followerLabel);
    followerCol.appendChild(followerInput);

    const removeCol = document.createElement("div");
    removeCol.className = "col-md-1 d-flex justify-content-end";
    const removeButton = document.createElement("button");
    removeButton.type = "button";
    removeButton.className = "btn btn-outline-danger btn-sm";
    removeButton.innerHTML = "<i class='bi bi-trash'></i>";
    removeButton.addEventListener("click", () => {
      wrapper.remove();
      scheduleSave("platforms");
    });
    removeCol.appendChild(removeButton);

    row.appendChild(platformCol);
    row.appendChild(urlCol);
    row.appendChild(followerCol);
    row.appendChild(removeCol);
    wrapper.appendChild(row);

    [platformSelect, urlInput, followerInput].forEach((el) => {
      el.addEventListener("input", () => scheduleSave("platforms"));
      el.addEventListener("change", () => scheduleSave("platforms"));
    });

    return wrapper;
  }

  function initPlatforms(entries) {
    if (!platformList) {
      return;
    }
    platformList.innerHTML = "";
    const data = entries && entries.length ? entries : [{}];
    data.forEach((entry) => {
      platformList.appendChild(buildPlatformRow(entry));
    });
  }

  function collectPlatforms() {
    const entries = [];
    platformList.querySelectorAll("[data-platform-entry]").forEach((row) => {
      const platform = row.querySelector("[data-platform-type]").value;
      const url = row.querySelector("[data-platform-url]").value;
      const followers = row.querySelector("[data-platform-followers]").value;
      if (platform || url || followers) {
        entries.push({
          platform: platform,
          url: url,
          followers_range: followers,
        });
      }
    });
    return entries;
  }

  function collectStepData(step) {
    switch (step) {
      case "identity":
        return {
          display_name: document.querySelector("[data-field='display_name']").value,
          country: document.querySelector("[data-field='country']").value,
          primary_niches: document.querySelector("[data-field='primary_niches']").value,
        };
      case "platforms":
        return {
          platforms: collectPlatforms(),
        };
      case "content":
        return {
          content_style_tags: document.querySelector("[data-field='content_style_tags']").value,
          posting_frequency: document.querySelector("[data-field='posting_frequency']").value,
          open_to_gifting: document.querySelector("[data-field='open_to_gifting']").checked,
        };
      case "performance":
        return {};
      case "payouts":
        return {
          payout_method: document.querySelector("[data-field='payout_method']").value,
          paypal_email: document.querySelector("[data-field='paypal_email']").value,
          tax_info: document.querySelector("[data-field='tax_info']").value,
        };
      default:
        return {};
    }
  }

  function scheduleSave(step) {
    clearTimeout(saveTimeouts[step]);
    saveTimeouts[step] = setTimeout(() => {
      saveStep(step);
    }, 600);
  }

  async function saveStep(step, options = {}) {
    const payload = collectStepData(step);
    if (options.skip) {
      payload.skip = true;
    }
    try {
      const response = await fetch(`/api/creator/onboarding/${step}/`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": getCookie("csrftoken"),
        },
        body: JSON.stringify(payload),
      });
      if (!response.ok) {
        const errorData = await response.json();
        alert(errorData.detail || "Unable to save onboarding step.");
        return;
      }
      const status = await response.json();
      updateProgress(status);
      updateChecklist(status);
      if (status.onboarding_completed) {
        if (successBanner) {
          successBanner.classList.remove("d-none");
        }
        if (onboardingSection) {
          onboardingSection.querySelectorAll(".row, .progress, #onboarding-checklist").forEach((el) => {
            el.classList.add("d-none");
          });
        }
      }
      if (status.next_recommended_step) {
        showStep(status.next_recommended_step);
      }
      updatePerformance(status.performance_summary);
    } catch (error) {
      alert("Unable to save onboarding step.");
    }
  }

  function updatePerformance(summary) {
    if (!performanceMessage) {
      return;
    }
    if (summary) {
      performanceMessage.innerHTML = `Total earnings: <strong>$${summary.total_earnings.toFixed(
        2
      )}</strong><br>Sales: <strong>${summary.sales_count}</strong>`;
    } else {
      performanceMessage.textContent = "We’ll add this automatically after your first sale.";
    }
  }

  checklist.querySelectorAll("[data-step] button").forEach((button) => {
    button.addEventListener("click", () => {
      const step = button.closest("[data-step]").getAttribute("data-step");
      showStep(step);
    });
  });

  saveButtons.forEach((button) => {
    button.addEventListener("click", () => {
      const step = button.getAttribute("data-step");
      saveStep(step);
    });
  });

  skipButtons.forEach((button) => {
    button.addEventListener("click", () => {
      const step = button.getAttribute("data-step");
      saveStep(step, { skip: true });
    });
  });

  document.querySelectorAll("[data-field]").forEach((input) => {
    const step = input.closest("[data-step-panel]").getAttribute("data-step-panel");
    input.addEventListener("input", () => scheduleSave(step));
    input.addEventListener("change", () => scheduleSave(step));
  });

  if (addPlatformButton) {
    addPlatformButton.addEventListener("click", () => {
      platformList.appendChild(buildPlatformRow({}));
      scheduleSave("platforms");
    });
  }

  setProfileFields();
  initPlatforms(profile.platforms || []);
  updateProgress(onboarding);
  updateChecklist(onboarding);
  updatePerformance(onboarding.performance_summary);
  showStep(onboarding.next_recommended_step || "identity");
})();
