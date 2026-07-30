// High-fidelity startup test: loads the REAL React + Sigma SDK bundles, then the
// plugin's own inline script, and drives it the way the host does.
//
// This exists because the mock-based suite passed while the plugin was completely
// broken in Sigma three separate times. The mock injected `client` directly and
// hand-called onConfig, so it validated my assumptions instead of the real
// startup path: SDK global resolution, the React peer dep, and config.get().
const fs = require('fs');
const { JSDOM } = require('jsdom');

// Vendor bundles are cached beside this file on first run:
//   curl -sL https://unpkg.com/react@18.3.1/umd/react.production.min.js > .react.js
//   curl -sL https://unpkg.com/@sigmacomputing/plugin@1.2.0 > .sdk.js
// Run with:  npm i jsdom@29.1.1 && node plugins/_scaffold/test.js
const HERE  = __dirname;
const REACT = fs.readFileSync(HERE + '/.react.js', 'utf8');
const SDK   = fs.readFileSync(HERE + '/.sdk.js', 'utf8');
const FILE  = HERE + '/index.html';
const HTML  = fs.readFileSync(FILE, 'utf8');
const INLINE = HTML.match(/<script>([\s\S]*?)<\/script>/g).pop()
                   .replace(/^<script>|<\/script>$/g, '');

let pass = 0, fail = 0;
const ok = (n, c, x) => { c ? (pass++, console.log('  ✅', n))
                            : (fail++, console.log('  ❌', n, x !== undefined ? '→ ' + x : '')); };

// The config the workbook spec actually stores, verified via GET /spec.
const REAL_CONFIG = {
  source: { kind: 'element', elementId: 'tbl' },
  label: 'c-label', value: 'c-value',
  config: JSON.stringify({ title: 'Revenue by region',
                           theme: { accent: '#EB1700', radius: 16 },
                           number: { format: 'currency', currency: 'USD', abbreviate: true } }),
  editMode: 'false',
};

function boot({ withReact = true, hostConfig = REAL_CONFIG, embedded = false } = {}) {
  const dom = new JSDOM('<!doctype html><html><body></body></html>',
                        { runScripts: 'dangerously', pretendToBeVisual: true });
  const w = dom.window;
  // Rebuild the plugin's DOM exactly as the file declares it.
  w.document.body.innerHTML = HTML.split('<body>')[1].split('<script>')[0];

  if (withReact) { try { w.eval(REACT); } catch (e) {} }
  let sdkThrew = null;
  try { w.eval(SDK); } catch (e) { sdkThrew = e.message; }

  // Stand in for the Sigma host: back the real client's config API with the
  // stored workbook config. Everything else is the genuine SDK object.
  const real = w.SigmaPlugin && w.SigmaPlugin.client;
  if (real) {
    // Capture the REAL API surface before stubbing, then refuse any property the
    // genuine SDK does not have. The previous mock invented getElementData(),
    // which does not exist — so the suite happily passed while the plugin hung
    // on "Loading…" forever in Sigma. A stub must never be more permissive than
    // the thing it stands in for.
    const surface = (o) => new Set(Object.keys(o));
    const guard = (name, allowed, impl) => new Proxy(impl, {
      get(t, k) {
        if (typeof k === 'string' && !allowed.has(k) && !(k in Object.prototype)) {
          w.__apiViolation = `client.${name}.${k} does not exist on the real SDK`;
          throw new TypeError(w.__apiViolation);
        }
        return t[k];
      }
    });
    const elemsAllowed = surface(real.elements);
    const cfgAllowed   = surface(real.config);
    const styleAllowed = surface(real.style);

    const listeners = [];
    real.config = guard('config', cfgAllowed, {
      configureEditorPanel(){}, get: () => hostConfig,
      set(v){ w.__saved = v; }, getKey: k => hostConfig[k], setKey(){},
      subscribe(fn){ listeners.push(fn); return () => {}; },
    });
    real.elements = guard('elements', elemsAllowed, {
      subscribeToElementData(id, cb) {
        w.__subscribedWith = id;                  // assert the ARG SHAPE too
        setTimeout(() => cb({ 'c-label': ['West','South','East'],
                              'c-value': [731529620.61, 609874886.44, 481799526.75] }), 5);
        return () => { w.__unsubscribed = true; };
      },
      getElementColumns: () => Promise.resolve({}),
      subscribeToElementColumns(){ return () => {}; },
      fetchMoreElementData(){},
    });
    real.style = guard('style', styleAllowed,
      { get: () => Promise.resolve({ backgroundColor: '#ffffff' }), subscribe(){ return () => {}; } });
    w.__emit = (c) => listeners.forEach(f => f(c));
  }
  // Simulate living in an iframe, which is how the plugin distinguishes
  // "embedded in a workbook" (a null client is a BUG) from standalone dev
  // (a null client is expected, render the preview).
  if (embedded) { try { Object.defineProperty(w, 'parent', { value: {}, configurable: true }); } catch (e) {} }
  w.eval(INLINE);
  return { w, sdkThrew, real };
}

(async () => {
  console.log('\n=== A. the React peer dependency (the actual root cause) ===');
  {
    const { w, sdkThrew } = boot({ withReact: false, embedded: true });
    ok('without React the SDK factory THROWS', !!sdkThrew, sdkThrew && sdkThrew.slice(0, 50));
    ok('…leaving window.SigmaPlugin an EMPTY object',
       w.SigmaPlugin && Object.keys(w.SigmaPlugin).length === 0);
    await new Promise(r => setTimeout(r, 20));
    ok('EMBEDDED + no client → reports the failure instead of faking data',
       /Plugin SDK not found/.test(w.document.getElementById('msg').textContent),
       JSON.stringify(w.document.getElementById('msg').textContent.slice(0, 60)));
  }

  {
    // and the inverse: standalone (not embedded) SHOULD show the preview
    const { w } = boot({ withReact: false, embedded: false });
    await new Promise(r => setTimeout(r, 20));
    ok('standalone + no client → renders the synthetic preview (correct)',
       /550/.test(w.document.getElementById('viz').textContent));
  }

  console.log('\n=== B. with React: real client resolves ===');
  {
    const { w, sdkThrew, real } = boot();
    ok('SDK evaluates cleanly', !sdkThrew, sdkThrew);
    ok('window.SigmaPlugin.client is the real client object', !!real);
    ok('client exposes config/elements/style',
       !!(real && real.config && real.elements && real.style));
    await new Promise(r => setTimeout(r, 50));
    const viz = w.document.getElementById('viz').textContent;
    ok('renders REAL data, not the synthetic fallback', !/550/.test(viz), viz.trim().slice(0, 60));
    ok('sums the three regions to $1.8B', /1\.8B/.test(viz), viz.trim().slice(0, 60));
    ok('applies the title from the settings JSON', /Revenue by region/.test(viz),
       viz.trim().slice(0, 80));
    ok('currency formatting applied', /\$/.test(viz), viz.trim().slice(0, 40));
    ok('called ONLY APIs that exist on the real SDK', !w.__apiViolation, w.__apiViolation);
    ok('subscribed with the element id as a STRING, not the config object',
       typeof w.__subscribedWith === 'string' && w.__subscribedWith === 'tbl',
       JSON.stringify(w.__subscribedWith));
  }

  console.log('\n=== C. config.get() — the bug subscribe-only hid ===');
  {
    // Host never fires a change event; an already-configured workbook must still render.
    const { w } = boot();
    await new Promise(r => setTimeout(r, 50));
    ok('renders WITHOUT any change event (proves config.get() is used)',
       !/Select a source element/.test(w.document.getElementById('msg').textContent),
       JSON.stringify(w.document.getElementById('msg').textContent.slice(0, 60)));
  }

  console.log('\n=== D. later change events still update ===');
  {
    const { w } = boot();
    await new Promise(r => setTimeout(r, 40));
    w.__emit(Object.assign({}, REAL_CONFIG,
      { config: JSON.stringify({ title: 'Retitled', number: { abbreviate: false } }) }));
    await new Promise(r => setTimeout(r, 40));
    ok('subscribe() updates on change', /Retitled/.test(w.document.getElementById('viz').textContent),
       w.document.getElementById('viz').textContent.trim().slice(0, 60));
  }


  console.log('\n=== E. settings drawer (edit mode) ===');
  {
    const { w } = boot({ hostConfig: Object.assign({}, REAL_CONFIG, { editMode: 'true' }) });
    await new Promise(r => setTimeout(r, 40));
    ok('gear visible when editMode is the STRING "true"',
       w.document.getElementById('gear').hidden === false);
    w.document.getElementById('gear').click();
    ok('drawer opens', w.document.getElementById('drawer').classList.contains('open'));
    ok('prefilled with the full merged settings',
       JSON.parse(w.document.getElementById('json').value).number !== undefined);
    w.document.getElementById('json').value = '{ "title": "Renamed", "theme": { "accent": "#ff0000" } }';
    w.document.getElementById('save').click();
    ok('save writes back via config.set({config})',
       !!(w.__saved && typeof w.__saved.config === 'string'));
    ok('partial settings deep-merge over defaults', JSON.parse(w.__saved.config).title === 'Renamed');
    ok('accent applied to CSS',
       w.document.documentElement.style.getPropertyValue('--accent').trim() === '#ff0000');
    w.document.getElementById('gear').click();
    w.document.getElementById('json').value = '{ not json';
    w.document.getElementById('save').click();
    ok('invalid JSON is rejected, drawer stays open',
       /Invalid JSON/.test(w.document.getElementById('err').textContent) &&
       w.document.getElementById('drawer').classList.contains('open'));
  }

  console.log('\n=== F. foreground derived from workbook background ===');
  {
    for (const [bg, want] of [['#ffffff', '#191919'], ['#101010', '#ffffff']]) {
      const { w, real } = boot();
      if (real) real.style = { get: () => Promise.resolve({ backgroundColor: bg }), subscribe(){ return () => {}; } };
      w.eval(INLINE);
      await new Promise(r => setTimeout(r, 40));
      ok(`bg ${bg} -> fg ${want}`,
         w.document.documentElement.style.getPropertyValue('--fg').trim() === want,
         w.document.documentElement.style.getPropertyValue('--fg').trim());
    }
  }

  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
})();
