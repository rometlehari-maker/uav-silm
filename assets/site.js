(function(){
  var d = document; d.documentElement.classList.add('js');
  var reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  // tabs
  d.querySelectorAll('[data-tabs]').forEach(function(box, bi){
    var tabs = [].slice.call(box.querySelectorAll(':scope > .tablist > .tab'));
    var panels = [].slice.call(box.querySelectorAll(':scope > .panel'));
    var list = box.querySelector(':scope > .tablist');
    var ind = list.querySelector(':scope > .tab-ind');
    function moveInd(t){ if(!ind) return; ind.style.left = t.offsetLeft + 'px'; ind.style.width = t.offsetWidth + 'px'; }
    function fade(){ list.classList.toggle('scrollable', list.scrollWidth > list.clientWidth + 4 && list.scrollLeft + list.clientWidth < list.scrollWidth - 4); }
    function select(i, focus){
      tabs.forEach(function(t, j){ var on = i === j; t.setAttribute('aria-selected', on); t.tabIndex = on ? 0 : -1; panels[j].classList.toggle('active', on); });
      moveInd(tabs[i]); if(focus) tabs[i].focus();
      list.scrollTo({left: tabs[i].offsetLeft - 24, behavior: reduced ? 'auto' : 'smooth'});
    }
    tabs.forEach(function(t, i){
      var id = 'tab' + bi + '-' + i; t.id = id; panels[i].setAttribute('aria-labelledby', id); panels[i].id = id + 'p'; t.setAttribute('aria-controls', id + 'p'); t.type = 'button';
      t.addEventListener('click', function(){ select(i); });
      t.addEventListener('keydown', function(e){
        if(e.key === 'ArrowRight'){ e.preventDefault(); select((i + 1) % tabs.length, true); }
        if(e.key === 'ArrowLeft'){ e.preventDefault(); select((i - 1 + tabs.length) % tabs.length, true); }
      });
    });
    select(0);
    list.addEventListener('scroll', fade, {passive: true});
    window.addEventListener('resize', function(){ var a = box.querySelector('.tab[aria-selected="true"]'); if(a) moveInd(a); fade(); });
    if(d.fonts && d.fonts.ready) d.fonts.ready.then(function(){ var a = box.querySelector('.tab[aria-selected="true"]'); if(a) moveInd(a); fade(); });
    fade();
  });

  // hover / tap cards
  d.querySelectorAll('.hovercard').forEach(function(el){
    el.tabIndex = 0; el.setAttribute('role', 'button'); el.setAttribute('aria-expanded', 'false');
    function tog(){ var o = el.classList.toggle('open'); el.setAttribute('aria-expanded', o); }
    el.addEventListener('click', function(e){ if(e.target.closest('a')) return; tog(); });
    el.addEventListener('keydown', function(e){ if(e.key === 'Enter' || e.key === ' '){ e.preventDefault(); tog(); } });
  });

  // reveal blocks (sources, privacy)
  d.querySelectorAll('.reveal-btn').forEach(function(b){
    var g = b.parentNode; b.setAttribute('aria-expanded', 'false');
    b.addEventListener('click', function(){ var o = g.classList.toggle('open'); b.setAttribute('aria-expanded', o); });
  });

  // next-step filter
  var chips = d.querySelectorAll('.chip');
  chips.forEach(function(c){ c.addEventListener('click', function(){
    var f = c.dataset.f; chips.forEach(function(x){ x.setAttribute('aria-pressed', x === c); });
    d.querySelectorAll('#nextlog .row').forEach(function(r){ var o = r.dataset.o; r.classList.toggle('hide', !(f === 'all' || o === f || o === 'all')); });
  }); });

  // countdown
  var cd = d.getElementById('countdown');
  if(cd){
    var now = new Date(); var t = new Date(2026, 11, 8);
    var n = Math.ceil((t - new Date(now.getFullYear(), now.getMonth(), now.getDate())) / 86400000);
    cd.textContent = n > 1 ? n + ' days' : n === 1 ? '1 day' : n === 0 ? 'Today' : 'Presented';
  }

  // lightbox
  var lb = d.getElementById('lb'), lbi = lb.querySelector('img'), lbp = lb.querySelector('p'), lbFrom = null;
  function closeLb(){ if(!lb.classList.contains('on')) return; lb.classList.remove('on'); if(lbFrom) lbFrom.focus(); }
  d.querySelectorAll('img.zoom').forEach(function(im){
    im.tabIndex = 0; im.setAttribute('role', 'button');
    function open(){ lbFrom = im; lbi.src = im.currentSrc || im.src; lbi.alt = im.alt; lbp.textContent = im.dataset.cap || im.alt; lb.classList.add('on'); lb.querySelector('button').focus(); }
    im.addEventListener('click', open);
    im.addEventListener('keydown', function(e){ if(e.key === 'Enter' || e.key === ' '){ e.preventDefault(); open(); } });
  });
  lb.addEventListener('click', function(e){ if(e.target !== lbi) closeLb(); });

  // menu sheet
  var menu = d.getElementById('menu'), mbtn = d.querySelector('.topbar .menu-btn'), mcur = mbtn.querySelector('.cur');
  function openMenu(){ menu.classList.add('on'); menu.removeAttribute('inert'); mbtn.setAttribute('aria-expanded', 'true'); d.body.style.overflow = 'hidden'; var a = menu.querySelector('a.mi.on') || menu.querySelector('a.mi'); setTimeout(function(){ a.focus(); }, 60); }
  function closeMenu(){ if(!menu.classList.contains('on')) return; menu.classList.remove('on'); menu.setAttribute('inert', ''); mbtn.setAttribute('aria-expanded', 'false'); d.body.style.overflow = ''; mbtn.focus(); }
  mbtn.addEventListener('click', openMenu);
  menu.querySelector('[data-close]').addEventListener('click', closeMenu);
  menu.querySelectorAll('a').forEach(function(a){ a.addEventListener('click', function(){ menu.classList.remove('on'); menu.setAttribute('inert', ''); mbtn.setAttribute('aria-expanded', 'false'); d.body.style.overflow = ''; }); });
  d.addEventListener('keydown', function(e){ if(e.key === 'Escape'){ closeLb(); closeMenu(); } });

  // back to top
  var totop = d.querySelector('.totop');
  totop.addEventListener('click', function(){ window.scrollTo({top: 0, behavior: reduced ? 'auto' : 'smooth'}); d.querySelector('.brand').focus({preventScroll: true}); });

  // scroll progress, active section in nav, menu and rail
  var bar = d.getElementById('scrollbar'), hdr = d.querySelector('header.top');
  var secs = [].slice.call(d.querySelectorAll('main > section.s[id]'));
  var ids = secs.map(function(s){ return s.id; });
  var navLinks = [].slice.call(d.querySelectorAll('[data-nav] a[href^="#"]'));
  var ticking = false, lastCur = null;
  function onScroll(){
    ticking = false;
    var h = d.documentElement, y = h.scrollTop || d.body.scrollTop;
    bar.style.transform = 'scaleX(' + (y / Math.max(1, h.scrollHeight - h.clientHeight)) + ')';
    hdr.classList.toggle('scrolled', y > 8);
    totop.classList.toggle('on', y > 900);
    var cur = null, line = window.innerHeight * 0.35;
    secs.forEach(function(s, i){ if(s && s.getBoundingClientRect().top < line) cur = ids[i]; });
    if(cur !== lastCur){
      lastCur = cur;
      navLinks.forEach(function(a){ var on = a.getAttribute('href') === '#' + cur; a.classList.toggle('on', on); if(on) a.setAttribute('aria-current', 'true'); else a.removeAttribute('aria-current'); });
    }
  }
  window.addEventListener('scroll', function(){ if(!ticking){ ticking = true; requestAnimationFrame(onScroll); } }, {passive: true});
  onScroll();

  if(d.getElementById('silm-layout')){
  // balance calculator (works without WebGL; the 3D view listens to its events)
  var lay = JSON.parse(d.getElementById('silm-layout').textContent);
  var st = {batteryFront: 110, servoX0: 508};
  var LO = 325, HI = 390;
  function pct(x){ return Math.max(0, Math.min(100, (x - LO) / (HI - LO) * 100)) + '%'; }
  function calc(){
    var L = lay.air.map(function(a){ return [a[1], a[2]]; });
    lay.comp.forEach(function(c){
      var x0 = c.x0, x1 = c.x1;
      if(c.id === 'bat'){ x0 = st.batteryFront; x1 = x0 + 138; }
      if(c.id === 'tsv'){ x0 = st.servoX0; x1 = x0 + 24; }
      if(c.id === 'rods'){ var a = st.servoX0 + 24; L.push([6 * (803 - a) / 271, (a + 803) / 2]); return; }
      L.push([c.m, (x0 + x1) / 2]);
    });
    if(st.servoX0 > 508) L.push([2 * (st.servoX0 - 520) * 0.011, (520 + st.servoX0) / 2]);
    var M = 0, S = 0; L.forEach(function(p){ M += p[0]; S += p[0] * p[1]; });
    var X = S / M, A = lay.aero;
    var c0 = A.Cm0 + A.CL0 * (X - A.xref) / A.MAC, c3 = A.Cm3 + A.CL3 * (X - A.xref) / A.MAC;
    var at = -c0 / ((c3 - c0) / 3), CL = A.CL0 + (A.CL3 - A.CL0) / 3 * at;
    return {M: M, X: X, SM: (A.NP - X) / A.MAC, V: Math.sqrt(2 * M / 1000 * 9.81 / (A.rho * A.S * CL))};
  }
  function out(k, v){ d.querySelectorAll('[data-out="' + k + '"]').forEach(function(e){ e.textContent = v; }); }
  var rng = d.querySelector('[data-ctl="battery"]');
  function update(){
    var r = calc();
    out('mass', Math.round(r.M) + ' g'); out('cg', r.X.toFixed(1) + ' mm'); out('sm', (r.SM * 100).toFixed(1) + '%'); out('trim', r.V.toFixed(1) + ' m/s');
    out('bat', 'front edge at ' + st.batteryFront + ' mm');
    var m = d.querySelector('[data-bar="cg"]'); if(m) m.style.left = pct(r.X);
    if(rng) rng.style.setProperty('--p', ((st.batteryFront - 110) / 11 * 100) + '%');
    var det = {batteryFront: st.batteryFront, servoX0: st.servoX0, cg: r.X};
    window.__silmLayout = det; window.dispatchEvent(new CustomEvent('silm:layout', {detail: det}));
    if(selId) showPick(selId);
  }
  if(rng) rng.addEventListener('input', function(){ st.batteryFront = +rng.value; update(); });
  d.querySelectorAll('[data-servo]').forEach(function(b){ b.addEventListener('click', function(){
    st.servoX0 = +b.dataset.servo; d.querySelectorAll('[data-servo]').forEach(function(x){ x.setAttribute('aria-pressed', x === b ? 'true' : 'false'); }); update();
  }); });

  // part selection: list <-> 3D view
  var rows = [].slice.call(d.querySelectorAll('table.comp tr[data-id]'));
  var card = d.getElementById('pickcard'), selId = null;
  function span(c){
    if(c.id === 'bat') return st.batteryFront + ' to ' + (st.batteryFront + 138) + ' mm';
    if(c.id === 'tsv') return st.servoX0 + ' to ' + (st.servoX0 + 24) + ' mm';
    if(c.id === 'rods') return (st.servoX0 + 24) + ' to 803 mm';
    if(c.id === 'wsv') return '430 mm, in the wing';
    if(c.id === 'wire') return 'about 335 mm';
    if(c.id === 'prop') return '860 mm';
    return c.x0 + ' to ' + c.x1 + ' mm';
  }
  function showPick(id){
    selId = id;
    rows.forEach(function(r){ r.classList.toggle('sel', r.dataset.id === id); r.setAttribute('aria-selected', r.dataset.id === id ? 'true' : 'false'); });
    var c = null; lay.comp.forEach(function(x){ if(x.id === id) c = x; });
    card.classList.toggle('has', !!c);
    card.querySelector('.pc-t').textContent = c ? c.name : 'Pick a part in the model or in the list below';
    card.querySelector('.pc-m').textContent = c ? (Math.round(c.m) + ' g, ' + span(c) + ' from the nose, ' + c.level.charAt(0).toLowerCase() + c.level.slice(1)) : '';
    card.querySelector('.pc-w').textContent = c ? c.why : '';
  }
  function pick(id){ var nid = id === selId ? null : id; showPick(nid); window.dispatchEvent(new CustomEvent('silm:select', {detail: {id: nid, source: 'page'}})); }
  rows.forEach(function(r){
    r.tabIndex = 0;
    r.addEventListener('click', function(){ pick(r.dataset.id); });
    r.addEventListener('keydown', function(e){ if(e.key === 'Enter' || e.key === ' '){ e.preventDefault(); pick(r.dataset.id); } });
  });
  window.addEventListener('silm:select', function(e){ if(e.detail.source === 'viewer') showPick(e.detail.id); });
  update();

  }

  // reveal on scroll
  if('IntersectionObserver' in window && !reduced){
    var io = new IntersectionObserver(function(es){ es.forEach(function(e){ if(e.isIntersecting){ e.target.classList.add('in'); io.unobserve(e.target); } }); }, {rootMargin: '0px 0px -6% 0px'});
    d.querySelectorAll('.rv').forEach(function(el){ io.observe(el); });
  } else { d.querySelectorAll('.rv').forEach(function(el){ el.classList.add('in'); }); }
})();
