const photoInput = document.querySelector("#photo");

if (photoInput) {
  const preview = document.querySelector("#preview");
  const image = document.querySelector("#preview-image");
  const remove = document.querySelector("#remove-photo");
  const action = document.querySelector("#photo-action");
  const optional = document.querySelector("#photo-optional");
  let previewUrl;

  function clearPreview() {
    if (previewUrl) {
      URL.revokeObjectURL(previewUrl);
      previewUrl = undefined;
    }
    image.removeAttribute("src");
    preview.hidden = true;
    action.textContent = "Add a photo";
    optional.hidden = false;
  }

  photoInput.addEventListener("change", () => {
    clearPreview();
    photoInput.setCustomValidity("");
    const file = photoInput.files[0];
    if (!file) return;
    if (file.size > 5 * 1024 * 1024) {
      photoInput.setCustomValidity("Choose a photo of 5 MB or less.");
      photoInput.reportValidity();
      return;
    }
    previewUrl = URL.createObjectURL(file);
    image.src = previewUrl;
    preview.hidden = false;
    action.textContent = "Replace photo";
    optional.hidden = true;
  });

  remove.addEventListener("click", () => {
    photoInput.value = "";
    photoInput.setCustomValidity("");
    clearPreview();
    photoInput.focus();
  });
}

const localTime = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" });

document.querySelectorAll("time[data-local-time]").forEach((element) => {
  const timestamp = new Date(element.dateTime);
  if (!Number.isNaN(timestamp.getTime())) element.textContent = localTime.format(timestamp);
});

const disconnectForm = document.querySelector("#disconnect-gmail");
if (disconnectForm) {
  disconnectForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = disconnectForm.querySelector("button");
    const errorMessage = document.querySelector("#gmail-account-error");
    button.disabled = true;
    errorMessage.hidden = true;
    try {
      const response = await fetch(disconnectForm.action, { method: "POST", body: new FormData(disconnectForm) });
      if (!response.ok) throw new Error();
      window.location.reload();
    } catch {
      errorMessage.textContent = "Gmail could not be disconnected. Try again.";
      errorMessage.hidden = false;
      button.disabled = false;
    }
  });
}

const draftText = () => `${document.querySelector("#draft-subject").value}\n\n${document.querySelector("#draft-body").value}\n`;
const downloadDraft = document.querySelector("#download-draft");

if (downloadDraft) {
  downloadDraft.hidden = false;
  downloadDraft.addEventListener("click", () => {
    const file = new Blob([draftText()], { type: "text/plain;charset=utf-8" });
    const link = document.createElement("a");
    const url = URL.createObjectURL(file);
    link.href = url;
    link.download = "fix-it-bulls-report.txt";
    link.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
}

const copyDraft = document.querySelector("#copy-draft");
if (copyDraft && navigator.clipboard) {
  copyDraft.hidden = false;
  copyDraft.addEventListener("click", async () => {
    const status = document.querySelector("#copy-status");
    try {
      await navigator.clipboard.writeText(draftText());
      status.textContent = "Report copied.";
    } catch {
      status.textContent = "Could not copy. Download the draft instead.";
    }
    status.hidden = false;
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
        const pill = document.querySelector("#tracking-status");
        const lastCheck = document.querySelector("#last-reply-check");
        if (replies.length) {
          list.replaceChildren();
          pill.textContent = "Reply received";
          pill.classList.replace("status-sent", "status-reply_received");
        }
        for (const reply of replies) {
          const item = document.createElement("li");
          const heading = document.createElement("strong");
          heading.textContent = "Reply received";
          const sender = document.createElement("span");
          sender.className = "reply-sender";
          sender.textContent = reply.sender;
          const time = document.createElement("time");
          time.dateTime = reply.received_at;
          time.textContent = localTime.format(new Date(reply.received_at));
          const message = document.createElement("p");
          message.textContent = reply.safe_text;
          item.append(heading, time, sender, message);
          list.append(item);
        }
        lastCheck.textContent = `Last checked: ${localTime.format(new Date())}`;
        lastCheck.hidden = false;
        showStatus(replies.length ? "Reply received" : "No replies found in this check.");
      } catch (error) {
        showStatus(error.message);
      } finally {
        check.disabled = false;
      }
    });
  }
}
