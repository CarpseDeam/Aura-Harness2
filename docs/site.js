/* Progressive enhancements for the static product tour. */
"use strict";

const tour = document.querySelector("[data-tour]");
if (tour) {
  const tabList = tour.querySelector(".tour-tabs");
  const tabs = Array.from(tabList.querySelectorAll("a"));
  const panels = tabs.map((tab) => document.getElementById(tab.hash.slice(1)));
  const narrow = window.matchMedia("(max-width: 760px)");

  function updateOrientation() {
    tabList.setAttribute("aria-orientation", narrow.matches ? "horizontal" : "vertical");
  }

  function selectTab(index, focus) {
    tabs.forEach((tab, i) => {
      const selected = i === index;
      tab.setAttribute("aria-selected", String(selected));
      tab.tabIndex = selected ? 0 : -1;
      panels[i].hidden = !selected;
    });
    if (focus) tabs[index].focus();
  }

  tabList.setAttribute("role", "tablist");
  tabs.forEach((tab, index) => {
    tab.setAttribute("role", "tab");
    tab.setAttribute("aria-controls", panels[index].id);
    panels[index].setAttribute("role", "tabpanel");
    panels[index].setAttribute("aria-labelledby", tab.id);
    panels[index].tabIndex = 0;
    tab.addEventListener("click", (event) => {
      event.preventDefault();
      selectTab(index, false);
    });
    tab.addEventListener("keydown", (event) => {
      let next = index;
      if (event.key === "ArrowRight" || event.key === "ArrowDown") next = (index + 1) % tabs.length;
      else if (event.key === "ArrowLeft" || event.key === "ArrowUp") next = (index + tabs.length - 1) % tabs.length;
      else if (event.key === "Home") next = 0;
      else if (event.key === "End") next = tabs.length - 1;
      else if (event.key === " " || event.key === "Enter") next = index;
      else return;
      event.preventDefault();
      selectTab(next, true);
    });
  });

  function selectFromHash() {
    const index = tabs.findIndex((tab) => tab.hash === window.location.hash);
    if (index !== -1) selectTab(index, false);
  }

  tour.classList.add("enhanced");
  updateOrientation();
  narrow.addEventListener("change", updateOrientation);
  selectTab(0, false);
  selectFromHash();
  window.addEventListener("hashchange", selectFromHash);
}

if (navigator.clipboard && window.isSecureContext) {
  document.querySelectorAll("[data-copy]").forEach((button) => {
    button.hidden = false;
    let resetTimer;
    button.addEventListener("click", async () => {
      const source = document.getElementById(button.dataset.copy);
      const status = document.getElementById("copy-status");
      clearTimeout(resetTimer);
      try {
        await navigator.clipboard.writeText(source.textContent.trim());
        button.textContent = "Copied";
        status.textContent = "Prompt copied. Paste it into Aura to create a reusable workflow.";
      } catch {
        const selection = window.getSelection();
        const range = document.createRange();
        range.selectNodeContents(source);
        selection.removeAllRanges();
        selection.addRange(range);
        status.textContent = "Copy was unavailable. The prompt is selected so you can copy it manually.";
      }
      resetTimer = window.setTimeout(() => { button.textContent = "Copy prompt"; }, 2500);
    });
  });
}
