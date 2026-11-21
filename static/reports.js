const photoInput = document.querySelector("#photo");

if (photoInput) {
  const preview = document.querySelector("#preview");
  const image = document.querySelector("#preview-image");
  const remove = document.querySelector("#remove-photo");
  let previewUrl;

  function clearPreview() {
    if (previewUrl) {
      URL.revokeObjectURL(previewUrl);
      previewUrl = undefined;
    }
    image.removeAttribute("src");
    preview.hidden = true;
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
  });

  remove.addEventListener("click", () => {
    photoInput.value = "";
    photoInput.setCustomValidity("");
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
