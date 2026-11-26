// Save links for prepared files when delivery could not be confirmed.
export function createPreparedDownloadView() {
  let recoveryURLs = [];
  function clearPreparedFiles() {
    for (const url of recoveryURLs) URL.revokeObjectURL(url);
    recoveryURLs = [];
    document.getElementById("downloads").replaceChildren();
    document.getElementById("downloads").hidden = true;
  }

  function showPreparedFiles(preparedResults) {
    const downloads = document.getElementById("downloads");
    if (!preparedResults?.length || recoveryURLs.length) return;
    const note = document.createElement("p");
    note.textContent =
      "Your prepared files are still on this device. Save them before closing this page.";
    downloads.append(note);
    for (const result of preparedResults) {
      const link = document.createElement("a");
      link.href = URL.createObjectURL(result.blob);
      recoveryURLs.push(link.href);
      link.download = result.name;
      link.className = "download-link";
      const label = document.createElement("span");
      label.textContent = "Save video " + result.state.index + " · " + result.name;
      const icon = document.createElement("span");
      icon.className = "icon icon-download";
      icon.setAttribute("aria-hidden", "true");
      link.append(label, icon);
      downloads.append(link);
    }
    downloads.hidden = false;
  }

  return { clear: clearPreparedFiles, show: showPreparedFiles };
}
