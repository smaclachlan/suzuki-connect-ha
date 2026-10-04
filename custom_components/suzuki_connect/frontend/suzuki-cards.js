/*
 * Suzuki Connect dashboard cards, served and registered by the integration.
 *
 *   type: custom:suzuki-recent-trips-card       entity: <Trip last distance sensor>
 *   type: custom:suzuki-charging-sessions-card  entity: <Charging last session sensor>
 *
 * Both read a list attribute (recent_trips / recent_sessions) that the
 * integration keeps out of the recorder. Plain JS, no build step.
 */

const DOCS = "https://github.com/smaclachlan/suzuki-connect-ha#dashboard-cards";

function esc(value) {
  return String(value).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);
}

function has(value) {
  return value !== null && value !== undefined && value !== "";
}

function duration(minutes) {
  if (!has(minutes)) return "–";
  const total = Math.round(Number(minutes));
  const h = Math.floor(total / 60);
  const m = total % 60;
  return h ? `${h} h ${String(m).padStart(2, "0")} min` : `${m} min`;
}

function withUnit(value, unit) {
  return has(value) ? `${value}${unit ? " " + unit : ""}` : "–";
}

function timeOptions(hass) {
  // Follow the user's "use server/browser time zone" profile setting.
  const local = hass.locale && hass.locale.time_zone === "local";
  return local ? {} : { timeZone: hass.config.time_zone };
}

function language(hass) {
  return (hass.locale && hass.locale.language) || hass.language || navigator.language;
}

function dateCell(hass, iso) {
  if (!iso) return "–";
  return new Date(iso).toLocaleDateString(language(hass), Object.assign(
    { weekday: "short", day: "2-digit", month: "short" }, timeOptions(hass)));
}

function timeCell(hass, iso) {
  if (!iso) return "–";
  return new Date(iso).toLocaleTimeString(language(hass), Object.assign(
    { hour: "2-digit", minute: "2-digit" }, timeOptions(hass)));
}

const STYLE = `
  ha-card { overflow: hidden; }
  .content { padding: 0 16px 16px; }
  table { width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; }
  th {
    text-align: left; font-weight: 500; font-size: 0.85em;
    color: var(--secondary-text-color); padding: 4px 6px 6px;
    border-bottom: 1px solid var(--divider-color);
  }
  td { padding: 6px; border-bottom: 1px solid var(--divider-color); white-space: nowrap; }
  tr:last-child td { border-bottom: none; }
  .num { text-align: right; }
  .muted { color: var(--secondary-text-color); }
  .message { color: var(--secondary-text-color); padding: 8px 0; }
  .warning { color: var(--warning-color, #ffa600); padding: 8px 0; }
`;

class SuzukiHistoryCard extends HTMLElement {
  setConfig(config) {
    if (!config || !config.entity) {
      throw new Error("Choose a Suzuki Connect sensor (entity)");
    }
    this._config = Object.assign({ max: 10 }, config);
    this._stateObj = undefined;
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    const stateObj = this._config ? hass.states[this._config.entity] : undefined;
    if (stateObj !== this._stateObj) {
      this._stateObj = stateObj;
      this._render();
    }
  }

  getCardSize() {
    return 2 + Math.min(this._rows().length, (this._config && this._config.max) || 10);
  }

  getGridOptions() {
    return { columns: 12, min_columns: 6 };
  }

  static getConfigElement() {
    return document.createElement("suzuki-history-card-editor");
  }

  static stubFor(hass, attribute) {
    const id = Object.keys(hass.states).find(
      (eid) => eid.startsWith("sensor.") && hass.states[eid].attributes[attribute] !== undefined,
    );
    return { entity: id || "" };
  }

  _rows() {
    const stateObj = this._stateObj;
    const rows = stateObj ? stateObj.attributes[this.constructor.attribute] : undefined;
    return Array.isArray(rows) ? rows : [];
  }

  _render() {
    if (!this._config) return;
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    const title = this._config.title !== undefined ? this._config.title : this.constructor.defaultTitle;
    let body;
    if (!this._hass) {
      body = "";
    } else if (!this._stateObj) {
      body = `<div class="warning">Entity not found: ${esc(this._config.entity)}</div>`;
    } else if (this._stateObj.attributes[this.constructor.attribute] === undefined) {
      body = `<div class="message">${esc(this.constructor.emptyText)}</div>`;
    } else {
      const rows = this._rows().slice(0, this._config.max);
      body = rows.length
        ? this._table(rows)
        : `<div class="message">${esc(this.constructor.emptyText)}</div>`;
    }
    this.shadowRoot.innerHTML = `
      <style>${STYLE}</style>
      <ha-card header="${esc(title || "")}">
        <div class="content">${body}</div>
      </ha-card>`;
  }

  _table(rows) {
    const columns = this.constructor.columns;
    const head = columns.map((c) => `<th class="${c.num ? "num" : ""}">${esc(c.label)}</th>`).join("");
    const lines = rows.map((row) => "<tr>" + columns.map((c) =>
      `<td class="${c.num ? "num" : ""}">${esc(c.value(row, this._hass))}</td>`).join("") + "</tr>");
    return `<table><thead><tr>${head}</tr></thead><tbody>${lines.join("")}</tbody></table>`;
  }
}

class SuzukiRecentTripsCard extends SuzukiHistoryCard {
  static getStubConfig(hass) {
    return SuzukiHistoryCard.stubFor(hass, "recent_trips");
  }
}
SuzukiRecentTripsCard.attribute = "recent_trips";
SuzukiRecentTripsCard.defaultTitle = "Recent trips";
SuzukiRecentTripsCard.emptyText =
  "No trips yet. Turn on 'Fetch trips, charging history, schedules and subscription' " +
  "in the Suzuki Connect options, and choose the Trip last distance sensor.";
SuzukiRecentTripsCard.columns = [
  { label: "Date", value: (t, hass) => dateCell(hass, t.start) },
  { label: "Time", value: (t, hass) => timeCell(hass, t.start) },
  { label: "Distance", num: true, value: (t) => withUnit(t.distance, t.distance_unit) },
  { label: "Duration", num: true, value: (t) => duration(t.duration_minutes) },
  {
    label: "Efficiency", num: true,
    value: (t) => withUnit(t.average_consumption, t.average_consumption_unit),
  },
  { label: "Battery", num: true, value: (t) => has(t.battery_used_pct) ? `${t.battery_used_pct}%` : "–" },
];

class SuzukiChargingSessionsCard extends SuzukiHistoryCard {
  static getStubConfig(hass) {
    return SuzukiHistoryCard.stubFor(hass, "recent_sessions");
  }
}
SuzukiChargingSessionsCard.attribute = "recent_sessions";
SuzukiChargingSessionsCard.defaultTitle = "Charging sessions";
SuzukiChargingSessionsCard.emptyText =
  "No charging sessions yet. Turn on 'Fetch trips, charging history, schedules and " +
  "subscription' in the Suzuki Connect options, and choose the Charging last session sensor.";
SuzukiChargingSessionsCard.columns = [
  { label: "Date", value: (s, hass) => dateCell(hass, s.start) },
  { label: "Time", value: (s, hass) => timeCell(hass, s.start) },
  {
    label: "Charge", num: true,
    value: (s) => has(s.start_level) && has(s.end_level) ? `${s.start_level}% → ${s.end_level}%` : "–",
  },
  { label: "Energy", num: true, value: (s) => withUnit(s.energy, "kWh") },
  { label: "Duration", num: true, value: (s) => duration(s.duration_minutes) },
  { label: "Type", value: (s) => has(s.charge_type) ? s.charge_type : "–" },
];

const EDITOR_SCHEMA = [
  { name: "entity", required: true, selector: { entity: { filter: { integration: "suzuki_connect", domain: "sensor" } } } },
  { name: "title", selector: { text: {} } },
  { name: "max", selector: { number: { min: 1, max: 10, mode: "box" } } },
];
const EDITOR_LABELS = { entity: "Sensor", title: "Title", max: "Rows (up to 10)" };

class SuzukiHistoryCardEditor extends HTMLElement {
  setConfig(config) {
    this._config = config;
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    if (this._form) this._form.hass = hass;
  }

  _render() {
    if (!this._form) {
      this._form = document.createElement("ha-form");
      this._form.computeLabel = (schema) => EDITOR_LABELS[schema.name] || schema.name;
      this._form.addEventListener("value-changed", (ev) => {
        this._config = Object.assign({}, this._config, ev.detail.value);
        this.dispatchEvent(new CustomEvent("config-changed", {
          detail: { config: this._config }, bubbles: true, composed: true,
        }));
      });
      this.appendChild(this._form);
    }
    this._form.hass = this._hass;
    this._form.schema = EDITOR_SCHEMA;
    this._form.data = Object.assign({ max: 10 }, this._config);
  }
}

const CARDS = [
  ["suzuki-recent-trips-card", SuzukiRecentTripsCard, "Suzuki recent trips",
    "Your car's last trips: date, distance, duration, efficiency and battery used."],
  ["suzuki-charging-sessions-card", SuzukiChargingSessionsCard, "Suzuki charging sessions",
    "Your car's recent charging sessions: charge added, energy, duration and type."],
];

if (!customElements.get("suzuki-history-card-editor")) {
  customElements.define("suzuki-history-card-editor", SuzukiHistoryCardEditor);
}
window.customCards = window.customCards || [];
for (const [tag, cls, name, description] of CARDS) {
  if (!customElements.get(tag)) customElements.define(tag, cls);
  if (!window.customCards.some((card) => card.type === tag)) {
    window.customCards.push({ type: tag, name, description, preview: true, documentationURL: DOCS });
  }
}
