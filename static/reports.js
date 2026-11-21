const photoInput = document.querySelector("#photo");

if (photoInput) {
  const preview = document.querySelector("#preview");
  const image = document.querySelector("#preview-image");
  const remove = document.querySelector("#remove-photo");
  const cameraInput = document.querySelector("#camera-file");
  const openCamera = document.querySelector("#open-camera");
  const cameraPanel = document.querySelector("#camera-panel");
  const video = document.querySelector("#camera-video");
  const capture = document.querySelector("#capture-photo");
  const closeCamera = document.querySelector("#cancel-camera");
  const cameraStatus = document.querySelector("#camera-status");
  let previewUrl;
  let stream;
  let cameraSession = 0;

  openCamera.hidden = false;

  function showCameraStatus(message) {
    cameraStatus.textContent = message;
    cameraStatus.hidden = !message;
  }

  function stopCamera() {
    cameraSession += 1;
    stream?.getTracks().forEach(track => track.stop());
    stream = undefined;
    video.srcObject = null;
    cameraPanel.hidden = true;
    openCamera.disabled = false;
    openCamera.setAttribute("aria-expanded", "false");
    capture.disabled = true;
  }

  function clearPreview() {
    if (previewUrl) {
      URL.revokeObjectURL(previewUrl);
      previewUrl = undefined;
    }
    image.removeAttribute("src");
    preview.hidden = true;
  }

  function previewPhoto(input) {
    const file = input.files[0];
    if (!file) return;
    stopCamera();
    showCameraStatus("");
    clearPreview();
    (input === photoInput ? cameraInput : photoInput).value = "";
    photoInput.setCustomValidity("");
    if (file.size > 5 * 1024 * 1024) {
      photoInput.setCustomValidity("Choose a photo of 5 MB or less.");
      photoInput.reportValidity();
      showCameraStatus("Choose a photo of 5 MB or less.");
      return;
    }
    previewUrl = URL.createObjectURL(file);
    image.src = previewUrl;
    preview.hidden = false;
  }

  photoInput.addEventListener("change", () => {
    if (photoInput.files.length) {
      previewPhoto(photoInput);
    } else if (!cameraInput.files.length) {
      clearPreview();
      photoInput.setCustomValidity("");
      showCameraStatus("");
    }
  });
  cameraInput.addEventListener("change", () => previewPhoto(cameraInput));

  openCamera.addEventListener("click", async () => {
    showCameraStatus("");
    if (!navigator.mediaDevices?.getUserMedia || typeof DataTransfer !== "function") {
      cameraInput.click();
      return;
    }
    const session = ++cameraSession;
    cameraPanel.hidden = false;
    openCamera.setAttribute("aria-expanded", "true");
    openCamera.disabled = true;
    closeCamera.focus();
    showCameraStatus("Starting the camera…");
    try {
      const feed = await navigator.mediaDevices.getUserMedia({
        audio: false, video: { facingMode: { ideal: "environment" }, width: { ideal: 1920 } },
      });
      if (session !== cameraSession) {
        feed.getTracks().forEach(track => track.stop());
        return;
      }
      stream = feed;
      video.srcObject = feed;
      await video.play();
      if (session === cameraSession) {
        capture.disabled = false;
        showCameraStatus("");
      }
    } catch {
      if (session !== cameraSession) return;
      stopCamera();
      showCameraStatus("Camera unavailable. You can upload a photo instead.");
      openCamera.focus();
    }
  });

  capture.addEventListener("click", () => {
    if (!stream || !video.videoWidth || !video.videoHeight) return;
    capture.disabled = true;
    const session = cameraSession;
    const scale = Math.min(1, 1920 / Math.max(video.videoWidth, video.videoHeight));
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(video.videoWidth * scale));
    canvas.height = Math.max(1, Math.round(video.videoHeight * scale));
    canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);
    canvas.toBlob(blob => {
      if (session !== cameraSession) return;
      if (!blob) {
        capture.disabled = false;
        showCameraStatus("Could not capture the photo. Try again or upload a photo.");
        return;
      }
      const selection = new DataTransfer();
      selection.items.add(new File([blob], "camera-photo.jpg", { type: "image/jpeg" }));
      photoInput.files = selection.files;
      previewPhoto(photoInput);
      showCameraStatus("Photo captured.");
      remove.focus();
    }, "image/jpeg", 0.86);
  });

  closeCamera.addEventListener("click", () => {
    stopCamera();
    showCameraStatus("");
    openCamera.focus();
  });
  photoInput.form.addEventListener("submit", stopCamera);
  window.addEventListener("pagehide", stopCamera);

  remove.addEventListener("click", () => {
    photoInput.value = "";
    cameraInput.value = "";
    photoInput.setCustomValidity("");
    showCameraStatus("");
    clearPreview();
    photoInput.focus();
  });
}

const downloadDraft = document.querySelector("#download-draft");

if (downloadDraft) {
  downloadDraft.hidden = false;
  downloadDraft.addEventListener("click", () => {
    const subject = document.querySelector("#draft-subject").value;
    const body = document.querySelector("#draft-body").value;
    const file = new Blob([`${subject}\n\n${body}\n`], { type: "text/plain;charset=utf-8" });
    const link = document.createElement("a");
    const url = URL.createObjectURL(file);
    link.href = url;
    link.download = "fix-it-bulls-report.txt";
    link.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
}

document.querySelectorAll("button[data-pending]").forEach((button) => {
  button.form.addEventListener("submit", () => {
    button.disabled = true;
    button.textContent = button.dataset.pending;
    button.form.setAttribute("aria-busy", "true");
  });
});

const mailControls = document.querySelector("#mail-controls");

if (mailControls) {
  const status = document.querySelector("#mail-status");
  const guidance = document.querySelector("#mail-guidance");
  const subject = document.querySelector("#draft-subject");
  const body = document.querySelector("#draft-body");
  const previewButton = document.querySelector("#preview-email");
  const preview = document.querySelector("#email-preview");
  const send = document.querySelector("#send-email");
  const check = document.querySelector("#check-replies");
  const version = Number(mailControls.dataset.reportVersion);
  let approval;

  function showStatus(message) {
    status.textContent = message;
    status.hidden = !message;
  }

  function hidePreview() {
    approval = undefined;
    preview.hidden = true;
  }

  async function post(url, data) {
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRFToken": mailControls.dataset.csrf },
      body: JSON.stringify(data),
    });
    const result = await response.json().catch(() => ({}));
    if (response.ok) return result;
    if (result.code === "urgent_guidance") {
      if (previewButton) {
        previewButton.hidden = true;
        hidePreview();
      }
      guidance.href = result.guidance_url;
      guidance.hidden = false;
    }
    throw new Error(result.error || "This action failed. Refresh the page and try again.");
  }

  if (previewButton) {
    previewButton.hidden = false;
    subject.addEventListener("input", hidePreview);
    body.addEventListener("input", hidePreview);
    previewButton.addEventListener("click", async () => {
      hidePreview();
      previewButton.disabled = true;
      showStatus("Preparing test email…");
      const draft = { subject: subject.value, body: body.value };
      try {
        const result = await post(mailControls.dataset.reviewUrl, {
          ...draft, report_version: version, issue_type: mailControls.dataset.issueType,
        });
        if (subject.value !== draft.subject || body.value !== draft.body) {
          showStatus("The draft changed. Open a new review.");
          return;
        }
        approval = result;
        const details = document.querySelector("#email-details");
        details.replaceChildren();
        for (const [label, value] of [
          ["From", result.sender], ["Test recipient", result.recipient],
          ["Subject", result.subject], ["Attachment", result.photo_id ? "Report photo" : "None"],
        ]) {
          const term = document.createElement("dt");
          term.textContent = label;
          const description = document.createElement("dd");
          description.textContent = value;
          details.append(term, description);
        }
        document.querySelector("#email-body").textContent = result.body;
        preview.hidden = false;
        showStatus("");
        send.focus();
      } catch (error) {
        showStatus(error.message);
      } finally {
        previewButton.disabled = false;
      }
    });
    send.addEventListener("click", async () => {
      if (!approval) return;
      const approved = approval;
      send.disabled = true;
      previewButton.disabled = true;
      showStatus("Sending test email…");
      try {
        const result = await post(mailControls.dataset.sendUrl, {
          approved_version: approved.report_version, approval_id: approved.approval_id,
        });
        if (result.state === "send_failed") {
          hidePreview();
          showStatus("Gmail rejected this send. Review the email again to retry.");
        } else {
          window.location.reload();
        }
      } catch (error) {
        showStatus(error.message);
      } finally {
        send.disabled = false;
        previewButton.disabled = false;
      }
    });
  }

  if (check) {
    check.hidden = false;
    check.addEventListener("click", async () => {
      check.disabled = true;
      showStatus("Checking the Gmail conversation…");
      try {
        const { replies } = await post(mailControls.dataset.repliesUrl, {});
        const list = document.querySelector("#reply-list");
        list.replaceChildren();
        for (const reply of replies) {
          const item = document.createElement("li");
          const heading = document.createElement("strong");
          heading.textContent = reply.sender;
          const time = document.createElement("time");
          time.dateTime = reply.received_at;
          time.textContent = new Date(reply.received_at).toLocaleString();
          const message = document.createElement("p");
          message.textContent = reply.safe_text;
          item.append(heading, time, message);
          list.append(item);
        }
        showStatus(replies.length ? "Reply received" : "No replies yet.");
      } catch (error) {
        showStatus(error.message);
      } finally {
        check.disabled = false;
      }
    });
  }
}
