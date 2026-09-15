const button = document.getElementById("action-button");

chrome.storage.local.get(["enabled"], (result) => {
    button.checked = result.enabled;
})

button.onclick = (e) => {
    let isEnabled = button.checked;
    chrome.storage.local.set({ enabled: isEnabled });
};


