import { mountResearchViewer } from "./research-view.js";
const viewer = mountResearchViewer(document);
const form = document.querySelector("#research-form");
form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const submit = document.querySelector("#research-submit");
  if (submit.disabled) return;
  submit.disabled = true;
  let token = document.querySelector("#capability").value;
  document.querySelector("#capability").value = "";
  try {
    await viewer.load(
      JSON.parse(document.querySelector("#research-command").value),
      token,
    );
  } catch {
    viewer.clear();
    document.querySelector("#research-status").textContent =
      "DATA_UNAVAILABLE | Invalid approved command";
  } finally {
    token = "";
    submit.disabled = false;
  }
});
