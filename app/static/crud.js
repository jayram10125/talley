/* CRUD controls for Tally masters and vouchers. This file shares the page state in app.js. */
const recordEntity = {
  ledgers: "ledgers", parties: "parties", "stock-groups": "stock-groups",
  "stock-items": "stock-items", "stock-summary": "stock-items",
  vouchers: "vouchers", sales: "vouchers", purchases: "vouchers", "day-book": "vouchers"
};
let editingRow = null;
let editingMode = "create";

function writeNotice(text, kind) {
  const element = $("write-message");
  element.textContent = text;
  element.className = "message " + kind;
}

function inputField(key, title, options = {}) {
  const label = document.createElement("label");
  label.textContent = title;
  label.className = options.wide ? "wide" : "";
  const input = document.createElement(options.multiline ? "textarea" : "input");
  input.id = "record-" + key;
  if (!options.multiline) input.type = options.type || "text";
  input.value = options.value ?? "";
  input.required = Boolean(options.required);
  if (options.placeholder) input.placeholder = options.placeholder;
  label.append(input);
  return label;
}

function formValue(key) { return $("record-" + key).value.trim(); }
function dateForInput(value) { return /^\d{8}$/.test(value || "") ? `${value.slice(0,4)}-${value.slice(4,6)}-${value.slice(6,8)}` : ""; }

function lineRow(inventory = false) {
  const row = document.createElement("div");
  row.className = inventory ? "inventory-row" : "line-row";
  if (inventory) {
    row.innerHTML = '<label>Stock item<input class="stock-name" required></label><label>Qty<input class="stock-qty" type="number" min="0.0001" step="any" required></label><label>Unit<input class="stock-unit" placeholder="Nos" required></label><label>Rate<input class="stock-rate" type="number" min="0" step="any" required></label><label>Sales/Purchase ledger<input class="stock-ledger" required></label><label>Godown (optional)<input class="stock-godown"></label><label>Batch (optional)<input class="stock-batch"></label>';
  } else {
    row.innerHTML = '<label>Ledger name<input class="line-ledger" required></label><label>Signed amount<input class="line-amount" type="number" step="any" required></label><label>Bill ref (optional)<input class="line-bill"></label><label class="check-label"><input class="line-party" type="checkbox"> Party</label>';
  }
  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "small-button remove";
  remove.textContent = "Remove";
  remove.addEventListener("click", () => row.remove());
  row.append(remove);
  return row;
}

function voucherLines(fields, edit) {
  const section = document.createElement("div");
  section.className = "form-section";
  if (edit) {
    const checkbox = document.createElement("label");
    checkbox.className = "check-label";
    checkbox.innerHTML = '<input id="replace-lines" type="checkbox"> Replace voucher ledger/stock lines';
    section.append(checkbox);
  }
  const content = document.createElement("div");
  content.id = "voucher-lines-content";
  if (edit) content.classList.add("hidden");
  content.innerHTML = '<h3>Ledger entries</h3><p class="section-note">Debit negative, credit positive. Total must be zero. Example: customer -1000, Sales +1000.</p><div id="ledger-lines"></div><button id="add-ledger-line" type="button" class="small-button">+ Add ledger line</button><div class="form-section"><h3>Stock lines (optional)</h3><p class="section-note">For Sales/Purchase invoices, enter item, quantity, unit, rate and accounting ledger.</p><div id="stock-lines"></div><button id="add-stock-line" type="button" class="small-button">+ Add stock line</button></div>';
  section.append(content);
  fields.append(section);
  $("add-ledger-line").addEventListener("click", () => $("ledger-lines").append(lineRow()));
  $("add-stock-line").addEventListener("click", () => $("stock-lines").append(lineRow(true)));
  if (edit) $("replace-lines").addEventListener("change", event => {
    content.classList.toggle("hidden", !event.target.checked);
    if (event.target.checked && !$("ledger-lines").children.length) $("ledger-lines").append(lineRow(), lineRow());
    content.querySelectorAll("input").forEach(input => { input.disabled = !event.target.checked; });
  });
  if (!edit) { $("ledger-lines").append(lineRow(), lineRow()); }
}

function openRecordForm(mode, row = null) {
  if (!connected || !$("company").value) { writeNotice("Pehle Tally connect karke company select karein.", "error"); return; }
  editingMode = mode;
  editingRow = row;
  const entity = recordEntity[activeView];
  const fields = $("record-fields");
  fields.replaceChildren();
  $("record-form-error").classList.add("hidden");
  $("record-modal-title").textContent = (mode === "create" ? "Create " : "Edit ") + (entity === "vouchers" ? "Voucher" : entity.replace("-", " "));
  $("record-modal-hint").textContent = entity === "vouchers"
    ? "Voucher changes Tally accounting data. Verify the company, date and balanced ledger lines before saving."
    : "This change will be written to the selected Tally company.";
  const grid = document.createElement("div");
  grid.className = "form-grid";
  fields.append(grid);
  if (entity === "vouchers") {
    grid.append(inputField("date", "Voucher date", {type:"date", value:dateForInput(row?.Date) || new Date().toISOString().slice(0,10), required:true}));
    grid.append(inputField("voucher_type", "Voucher type", {value:row?.VoucherTypeName || (activeView === "sales" ? "Sales" : activeView === "purchases" ? "Purchase" : "Journal"), required:true}));
    grid.append(inputField("voucher_number", "Voucher number (optional on create)", {value:row?.VoucherNumber || ""}));
    grid.append(inputField("narration", "Narration", {value:row?.Narration || "", multiline:true, wide:true}));
    voucherLines(fields, mode === "edit");
  } else {
    grid.append(inputField("name", "Name", {value:row?.Name || "", required:true}));
    grid.append(inputField("parent", "Group / Parent", {value:row?.Parent || (entity === "parties" ? "Sundry Debtors" : entity === "stock-groups" || entity === "stock-items" ? "Primary" : ""), required:entity === "ledgers" || entity === "parties"}));
    if (entity === "stock-items") {
      grid.append(inputField("base_units", "Base unit", {value:row?.BaseUnits || "", placeholder:"Nos"}));
      if (mode === "create") {
        grid.append(inputField("opening_quantity", "Opening quantity", {placeholder:"8 Nos"}));
        grid.append(inputField("opening_rate", "Opening rate", {placeholder:"30000/Nos"}));
        grid.append(inputField("opening_value", "Opening value", {placeholder:"240000"}));
      }
    }
    if (entity === "ledgers" || entity === "parties") grid.append(inputField("opening_balance", "Opening balance", {value:row?.OpeningBalance || ""}));
  }
  $("record-modal").classList.remove("hidden");
  fields.querySelector("input")?.focus();
}

function collectRecord() {
  const target = endpoint();
  const entity = recordEntity[activeView];
  const body = {...target, company:$("company").value};
  if (entity === "vouchers") {
    Object.assign(body, {date:formValue("date"), voucher_type:formValue("voucher_type"), voucher_number:formValue("voucher_number") || null, narration:formValue("narration")});
    if (editingMode === "edit") {
      Object.assign(body, {original_date:dateForInput(editingRow.Date), original_type:editingRow.VoucherTypeName, original_number:editingRow.VoucherNumber});
    }
    if (editingMode === "create" || $("replace-lines").checked) {
      body.ledger_entries = [...$("ledger-lines").children].map(row => ({ledger_name:row.querySelector(".line-ledger").value.trim(), amount:row.querySelector(".line-amount").value, is_party:row.querySelector(".line-party").checked, bill_reference:row.querySelector(".line-bill").value.trim() || null}));
      body.inventory_entries = [...$("stock-lines").children].map(row => ({stock_item:row.querySelector(".stock-name").value.trim(), quantity:row.querySelector(".stock-qty").value, unit:row.querySelector(".stock-unit").value.trim(), rate:row.querySelector(".stock-rate").value, ledger_name:row.querySelector(".stock-ledger").value.trim(), godown:row.querySelector(".stock-godown").value.trim() || null, batch_name:row.querySelector(".stock-batch").value.trim() || null}));
    }
  } else {
    Object.assign(body, {name:formValue("name"), parent:formValue("parent") || null});
    if (editingMode === "edit") body.original_name = editingRow.Name;
    if (entity === "stock-items") {
      body.base_units = formValue("base_units") || null;
      if (editingMode === "create") for (const key of ["opening_quantity","opening_rate","opening_value"]) body[key] = formValue(key) || null;
    }
    if (entity === "ledgers" || entity === "parties") body.opening_balance = formValue("opening_balance") || null;
  }
  return body;
}

async function saveRecord(event) {
  event.preventDefault();
  const entity = recordEntity[activeView];
  const button = $("save-record-button");
  button.disabled = true;
  try {
    const result = await request(`/api/records/${entity}`, {method:editingMode === "create" ? "POST" : "PATCH", headers:{"Content-Type":"application/json"}, body:JSON.stringify(collectRecord())});
    $("record-modal").classList.add("hidden");
    writeNotice(result.message, "success");
    await loadData();
  } catch(error) {
    $("record-form-error").textContent = errorText(error);
    $("record-form-error").classList.remove("hidden");
  } finally {button.disabled = false;}
}

async function deleteRecord(row) {
  const entity = recordEntity[activeView];
  const label = entity === "vouchers" ? `${row.VoucherTypeName} #${row.VoucherNumber} (${dateForInput(row.Date)})` : row.Name;
  if (!confirm(`Delete ${label} from Tally company ${$("company").value}? This cannot be undone here.`)) return;
  const target = endpoint();
  const body = {...target, company:$("company").value};
  if (entity === "vouchers") Object.assign(body, {date:dateForInput(row.Date), voucher_type:row.VoucherTypeName, voucher_number:row.VoucherNumber});
  else body.name = row.Name;
  try {
    const result = await request(`/api/records/${entity}`, {method:"DELETE", headers:{"Content-Type":"application/json"}, body:JSON.stringify(body)});
    writeNotice(result.message, "success");
    await loadData();
  } catch(error) {writeNotice(errorText(error), "error");}
}

const originalRenderRows = renderRows;
renderRows = function() {
  originalRenderRows();
  if ($("data-table").classList.contains("hidden")) return;
  const term = $("search").value.toLocaleLowerCase();
  const visible = term ? rows.filter(row => Object.values(row).some(value => String(value).toLocaleLowerCase().includes(term))) : rows;
  const header = document.createElement("th");
  header.textContent = "Actions";
  $("table-head").firstChild.append(header);
  [...$("table-body").children].forEach((tr, index) => {
    const cell = document.createElement("td");
    const actions = document.createElement("div");
    actions.className = "row-actions";
    const edit = document.createElement("button");
    edit.type = "button"; edit.className = "row-action"; edit.textContent = "Edit";
    edit.addEventListener("click", () => openRecordForm("edit", visible[index]));
    const remove = document.createElement("button");
    remove.type = "button"; remove.className = "row-action delete"; remove.textContent = "Delete";
    remove.addEventListener("click", () => deleteRecord(visible[index]));
    actions.append(edit, remove); cell.append(actions); tr.append(cell);
  });
};

$("new-record-button").addEventListener("click", () => openRecordForm("create"));
$("record-form").addEventListener("submit", saveRecord);
for (const id of ["close-record-modal", "cancel-record-modal"]) $(id).addEventListener("click", () => $("record-modal").classList.add("hidden"));
$("record-modal").addEventListener("click", event => {if (event.target === $("record-modal")) $("record-modal").classList.add("hidden")});
document.addEventListener("keydown", event => {if (event.key === "Escape") $("record-modal").classList.add("hidden")});
