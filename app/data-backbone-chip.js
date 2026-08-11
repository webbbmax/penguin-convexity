(function renderDataBackboneChip() {
  const chip = document.getElementById("opportunityBackboneStatus");
  const state = window.PENGUIN_CONVEXITY_DATA_BACKBONE;
  if (!chip || !state) return;
  const number = (value) => new Intl.NumberFormat("zh-CN").format(Number(value || 0));
  const schema = state.eventSchema || {};
  const continuity = state.continuity || {};
  chip.textContent = `${number(schema.normalizedEvents)} / ${number(schema.rawEvents)} 条已标准化 · ${number(continuity.openGaps)} 个开放断档`;
})();
