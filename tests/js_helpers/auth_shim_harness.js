// Runs the daemon's injected auth shim (read from argv[2]) against stubs and prints a JSON result.
// argv[3] is a JSON scenario: {stored, serverKey, laterStored}.
const vm = require('vm');
const fs = require('fs');
const src = fs.readFileSync(process.argv[2], 'utf8').replace(/^<script>/, '').replace(/<\/script>$/, '');
const sc = JSON.parse(process.argv[3]);
const store = {};
if (sc.stored) store.sma_api_key = sc.stored;
let dialogs = 0;
const calls = [];
const localStorage = {
  getItem: (k) => (k in store ? store[k] : null),
  setItem: (k, v) => { store[k] = v; },
  removeItem: (k) => { delete store[k]; },
};
const body = { appendChild: (n) => { if (n.id === 'sma-api-key-dialog') dialogs++; } };
const mk = () => ({ setAttribute() {}, appendChild() {}, remove() {}, focus() {}, addEventListener() {}, style: {} });
const win = {
  fetch: async (input, init) => {
    const key = init.headers.get('X-API-Key') || '';
    calls.push(key);
    if (sc.laterStored && calls.length === 1) store.sma_api_key = sc.laterStored;
    return { status: key && key === sc.serverKey ? 200 : 401 };
  },
};
win.window = win;
const ctx = vm.createContext({
  window: win, localStorage, sessionStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  Headers, Request: class {}, Object, Promise,
  document: { body, createElement: mk, addEventListener() {} },
});
vm.runInContext(src, ctx);
Promise.race([
  win.fetch('/jobs', {}).then((r) => r.status),
  new Promise((r) => setTimeout(() => r('pending'), 300)),
]).then((status) => {
  console.log(JSON.stringify({ status, dialogs, calls, stored: store.sma_api_key || '' }));
  process.exit(0);
});
