/**
 * ACCT TownHelper - imports a recognised town map into the CF TownHelper sheet.
 *
 * Setup: Extensions > Apps Script, paste this file into a new script file,
 * save, then reload the Google Sheet. A "TownHelper" menu appears at the top.
 */

// Codes used by ACCT TownHelper -> sheets of the document
var TH_SHEETS = {
  B: 'Buildings',
  C: 'Cliffs/Ramps/Grasslands',
  E: 'Beach',
  R: 'Rivers',
  L: 'River Start/Lakes'
};

function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('TownHelper')
    .addItem('Import map', 'thImportMap')
    .addToUi();
}

// Compares names ignoring spaces, slashes and case
function thNorm(s) {
  return String(s).toLowerCase().replace(/[^a-z0-9]/g, '');
}

function thFindSheet(ss, name) {
  var target = thNorm(name);
  var sheets = ss.getSheets();
  for (var i = 0; i < sheets.length; i++) {
    if (thNorm(sheets[i].getName()) === target) return sheets[i];
  }
  return null;
}

function thImportMap() {
  var ui = SpreadsheetApp.getUi();
  var ss = SpreadsheetApp.getActiveSpreadsheet();

  var answer = ui.prompt('Import map',
    'Paste the code copied from ACCT TownHelper:', ui.ButtonSet.OK_CANCEL);
  if (answer.getSelectedButton() !== ui.Button.OK) return;

  var code = answer.getResponseText().trim();
  if (code.indexOf('CFTH1|') !== 0) {
    ui.alert('This is not an ACCT TownHelper code (it must start with CFTH1|).');
    return;
  }
  var parts = code.substring(6).split(';');
  if (parts.length !== 25) {
    ui.alert('Incomplete code: ' + parts.length + ' acres instead of 25.');
    return;
  }

  var townMap = thFindSheet(ss, 'Town Map');
  if (!townMap) { ui.alert('Sheet "Town Map" not found.'); return; }

  // Check everything before changing anything
  var copies = [];
  for (var i = 0; i < 25; i++) {
    var m = parts[i].match(/^([BCERL])(\d+),(\d+)$/);
    if (!m) { ui.alert('Acre ' + (i + 1) + ' unreadable: ' + parts[i]); return; }
    var sheet = thFindSheet(ss, TH_SHEETS[m[1]]);
    if (!sheet) { ui.alert('Sheet "' + TH_SHEETS[m[1]] + '" not found.'); return; }
    copies.push({ sheet: sheet, row: Number(m[2]), col: Number(m[3]),
                  gy: Math.floor(i / 5), gx: i % 5 });
  }

  // Each acre = 16x16 block of cells, copied like a manual copy-paste
  copies.forEach(function (c) {
    var src = c.sheet.getRange(c.row, c.col, 16, 16);
    var dst = townMap.getRange(2 + 16 * c.gy, 2 + 16 * c.gx, 16, 16);
    src.copyTo(dst);
  });

  SpreadsheetApp.flush();
  ss.setActiveSheet(townMap);
  ui.alert('Map imported: 25 acres placed.');
}
