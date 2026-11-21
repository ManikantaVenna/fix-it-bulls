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
