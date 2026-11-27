/* AZIZ MINI — پنل برادری BBH (اپلیکیشن مجازی داخل تلگرام)
   داده‌ها محلی هستند (localStorage) و از روز اول کاملاً صفرند — هر کسی را خودت اضافه می‌کنی.
   همه‌ی متن‌ها فارسی و همه‌ی بخش‌ها تعاملی‌اند. */
'use strict';

const tg = window.Telegram?.WebApp || null;

/* ---------------------------------------------------------- ابزارها */
const faDigits = '۰۱۲۳۴۵۶۷۸۹';
const toFa = v => String(v).replace(/\d/g, d => faDigits[+d]);

const $ = sel => document.querySelector(sel);
const $$ = sel => [...document.querySelectorAll(sel)];

function showToast(text) {
  const el = $('#toast');
  el.textContent = text;
  el.classList.add('show');
  setTimeout(() => el.classList.remove('show'), 2400);
}

const haptic = kind => { try { tg?.HapticFeedback?.impactOccurred?.(kind || 'medium'); } catch (e) { /* ok */ } };

/* فیلد متنِ نرم — خودکار رشد می‌کند و با تمرکز، درخشش می‌گیرد */
const softField = el => {
  if (!el) return;
  const grow = () => {
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 220) + 'px';
  };
  el.addEventListener('input', grow, { passive: true });
  el.addEventListener('focus', grow);
  grow();
};

const STAT_KEYS = ['iq', 'comedy', 'simp', 'retardness'];
const STAT_META = {
  iq:         { en: 'IQ',         emoji: '💡' },
  comedy:     { en: 'COMEDY',     emoji: '😂' },
  simp:       { en: 'SIMPNESS',   emoji: '🥰' },
  retardness: { en: 'RETARDNESS', emoji: '🐢' },
};

/* ---------------------------------------------------------- وضعیت — دو حالته: زنده (داخل تلگرام) یا محلی */
const API_BASE = (window.BBH_API_BASE || '').replace(/\/+$/, '');
const tgUser = tg?.initDataUnsafe?.user || null;
const IN_TELEGRAM = !!tg && !!tg.initData && API_BASE;  // true = فقط داخل اپ
const LOCAL_KEY = 'bbh_mini_local_v3';

const loadLocalState = () => {
  try { return JSON.parse(localStorage.getItem(LOCAL_KEY)) || null; } catch (e) { return null; }
};
const saveLocalState = (s) => { try { localStorage.setItem(LOCAL_KEY, JSON.stringify(s)); } catch (e) { /* ignore */ } };
const defaultLocalState = () => ({ members: [], meId: null, judgments: [], news: null, strikeVotes: [], statRatings: {} });

const apiCall = async (path, opts = {}) => {
  if (!IN_TELEGRAM) return null;
  try {
    const res = await fetch(API_BASE + path, {
      ...opts,
      headers: {
        'Content-Type': 'application/json',
        'X-Init-Data': tg.initData,
        ...(opts.headers || {}),
      },
    });
    if (!res.ok) return null;
    return await res.json();
  } catch (e) { return null; }
};

const state = loadLocalState() || defaultLocalState();
let isAdmin = false;

const applyMembers = list => {
  state.members.length = 0;
  for (const m of list) {
    state.members.push({
      id: m.id, username: m.username || null, name: m.name,
      age: m.age ?? null, ability: m.ability || '',
      plus: m.plus || 0, warn: m.warn || 0, strike: m.strike || 0,
      stats: m.stats || { iq: 0, comedy: 0, simp: 0, retardness: 0 },
      ratingAvg: m.ratingAvg || { iq: 0, comedy: 0, simp: 0, retardness: 0 },
      final: m.final ?? 0, createdAt: m.createdAt || new Date().toISOString(),
    });
  }
  saveLocalState(state);
};

async function refreshState() {
  // حالت زنده — داخل تلگرام
  if (IN_TELEGRAM) {
    const meRes = await apiCall('/api/me');
    if (meRes) isAdmin = !!meRes.isAdmin;
    const res = await apiCall('/api/state');
    if (res && Array.isArray(res.members) && res.members.length > 0) {
      applyMembers(res.members);
      state.judgments = Array.isArray(res.history) ? res.history : [];
      saveLocalState(state);
      return true;
    }
    // اتصال ناموفق — به اسنپ‌شات Pages کوچ بده
    try {
      const snap = await fetch('js/snapshot.json', { cache: 'no-store' }).then(r => r.ok ? r.json() : null);
      if (snap && Array.isArray(snap.members)) {
        applyMembers(snap.members);
        return true;
      }
    } catch (e) { /* snapshot هم نشد */ }
    return false;
  }
  // حالت محلی — خارج تلگرام
  try {
    const snap = await fetch('js/snapshot.json', { cache: 'no-store' }).then(r => r.ok ? r.json() : null);
    if (snap && Array.isArray(snap.members)) {
      applyMembers(snap.members);
      return true;
    }
  } catch (e) { /* snapshot هم در دسترس نیست */ }
  return state.members.length > 0;
}

async function fetchNews() {
  if (!IN_TELEGRAM) {
    return state.news || [];
  }
  try {
    const res = await apiCall('/api/news');
    if (res && Array.isArray(res.news)) {
      state.news = res.news;
      saveLocalState(state);
      return res.news;
    }
    state.news = [];
    saveLocalState(state);
    return [];
  } catch (e) {
    return state.news || [];
  }
}

async function handleNewsPost() {
  const input = document.getElementById('newsContentInput');
  const note = document.getElementById('newsPostNote');
  const content = (input?.value || '').trim();
  if (content.length < 2) { if (note) note.textContent = '❌ متن خبر خیلی کوتاه است.'; return; }
  if (note) note.textContent = '⏳ در حال ثبت…';
  const ok = await postNews(content);
  if (ok) {
    if (input) input.value = '';
    if (note) note.textContent = '✅ خبر ثبت شد.';
  } else {
    if (note) note.textContent = '❌ ثبت ناموفق — دوباره تلاش کن.';
  }
}

async function postNews(content) {
  if (!IN_TELEGRAM) { 
    console.error('Not in Telegram. IN_TELEGRAM:', IN_TELEGRAM, 'tg:', !!tg, 'initData:', !!tg?.initData, 'API_BASE:', API_BASE);
    // Fallback: store in localStorage for testing
    if (!state.news) state.news = [];
    state.news.unshift({ id: Date.now(), content, author_name: tgUser?.first_name || 'Local', created_at: new Date().toISOString() });
    saveLocalState(state);
    renderNews();
    showToast('✅ خبر ثبت شد (حالت محلی).');
    return true;
  }
  try {
    const res = await apiCall('/api/news', { method: 'POST', body: JSON.stringify({ content }) });
    if (res && res.ok) {
      showToast('✅ خبر ثبت شد.');
      haptic('light');
      await fetchNews();
      renderNews();
      return true;
    } else {
      console.error('postNews failed:', res);
      showToast('❌ ثبت ناموفق — ' + (res?.error || 'دوباره تلاش کن.'));
      return false;
    }
  } catch (e) {
    console.error('postNews exception:', e);
    showToast('❌ خطای شبکه — دوباره تلاش کن.');
    return false;
  }
}

async function fetchStrikeVotes() {
  if (!IN_TELEGRAM) {
    return state.strikeVotes || [];
  }
  try {
    const res = await apiCall('/api/strike-votes');
    if (res && Array.isArray(res.votes)) {
      state.strikeVotes = res.votes;
      saveLocalState(state);
      return res.votes;
    }
    state.strikeVotes = [];
    saveLocalState(state);
    return [];
  } catch (e) {
    return state.strikeVotes || [];
  }
}

const MEMBERS = state.members;
const ME_ID = IN_TELEGRAM ? (tgUser?.id || null) : (state.meId || null);
const MY_MEMBER = () => MEMBERS.find(m => m.id === ME_ID) || null;

const finalOf = m => m.final ?? 0;

const AVATAR_COLORS = ['#2f8f5b', '#b45309', '#1d4ed8', '#7c3aed', '#db2777', '#0e7490', '#a16207', '#15803d'];
const colorFor = id => AVATAR_COLORS[Math.abs(Number(id) || 0) % AVATAR_COLORS.length];
const avatarHtml = (name, id, size = 40) => {
  const ch = (name || '؟').charAt(0);
  return `<div class="rank-avatar" style="width:${size}px;height:${size}px;background:${colorFor(id)};font-size:${Math.floor(size / 2)}px">${ch}</div>`;
};

const escapeHtml = str =>
  String(str).replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));

const viewRankOf = list => {
  const i = list.findIndex(m => m.id === ME_ID);
  return i >= 0 ? i + 1 : null;
};

/* ---------------------------------------------------------- ناوبری */
function showScreen(name) {
  $$('.screen').forEach(s => s.classList.remove('active'));
  const target = $('#screen-' + name);
  if (target) target.classList.add('active');
  $$('.nav-btn').forEach(b => b.classList.toggle('active', b.dataset.screen === name));
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

/* ---------------------------------------------------------- خانه */
function renderHome() {
  const me = MY_MEMBER();
  $('#heroFinal').textContent = me ? (finalOf(me) >= 0 ? '+' : '') + toFa(finalOf(me)) : '+۰';
  const plus = me ? me.plus : 0;
  $('#heroPlus').textContent = toFa(plus);
  $('#heroWarn').textContent = toFa(me ? me.warn : 0);
  $('#heroStrike').textContent = toFa(me ? me.strike : 0);
  $('#topPlus').textContent = toFa(plus);
  updateTopBar();

  if (!me) {
    $('#homeRankPreview').innerHTML =
      `<div class="empty-state">👤 هنوز ثبت‌نام نکرده‌ای!<br/>با دکمه‌ی <b>«ثبت‌نام»</b> در تب اعضا، وارد برادری شو.</div>`;
    $('#homeStatsPreview').innerHTML =
      `<div class="empty-state">اول ثبت‌نام کن، بعد آمارت صفر می‌شود و کم‌کم با رای‌گیری پر می‌شود.</div>`;
    return;
  }

  const sorted = [...MEMBERS].sort((a, b) => finalOf(b) - finalOf(a));
  const myPos = viewRankOf(sorted) || 1;
  const f = finalOf(me), sign = f >= 0 ? '+' : '';
  $('#homeRankPreview').innerHTML = `
    <div class="rank-item you">
      <div class="rank-pos">${toFa(myPos)}</div>
      ${avatarHtml(me.name, me.id, 44)}
      <div class="rank-body">
        <div class="rank-name">${escapeHtml(me.name)}</div>
        <div class="rank-sub">در جدول ${toFa(sorted.length)} نفری برادری</div>
      </div>
      <div class="rank-final ${f >= 0 ? 'plus' : 'minus'}">${sign}${toFa(f)}</div>
    </div>`;

  $('#homeStatsPreview').innerHTML = STAT_KEYS.map(k => {
    const meta = STAT_META[k];
    return `<div class="bar-row">
      <div class="bar-label">${meta.emoji} ${meta.en}</div>
      <div class="bar-track"><div class="bar-fill" data-fill="0" style="width:0%"></div></div>
      <div class="bar-value">${toFa(0)}٪</div>
    </div>`;
  }).join('');
}

function updateTopBar() {
  const me = MY_MEMBER();
  const nameBox = $('#topName');
  const avatarBox = $('#topAvatar');
  const rankBox = $('#topRank');
  if (me) {
    nameBox.textContent = me.name;
    avatarBox.textContent = (me.name || '؟').charAt(0);
    avatarBox.style.background = colorFor(me.id);
    const sorted = [...MEMBERS].sort((a, b) => finalOf(b) - finalOf(a));
    rankBox.textContent = `رتبه‌ی شما: ${toFa(viewRankOf(sorted))} از ${toFa(sorted.length)}`;
  } else {
    const tgName = [tgUser?.first_name, tgUser?.last_name].filter(Boolean).join(' ').trim();
    nameBox.textContent = tgName || 'سلام، عضو برادری!';
    avatarBox.textContent = (tgName || '؟').charAt(0);
    avatarBox.style.background = '#f59e0b';
    rankBox.textContent = 'ثبت‌نام کن تا رتبه بگیری';
  }
}

/* ---------------------------------------------------------- آمار */
function renderStats() {
  const me = MY_MEMBER();
  const nameEl = $('#statsName');
  if (nameEl) nameEl.textContent = me ? me.name : 'شما';

  const ability = (me && me.ability) || '';
  $('#abilityBanner').innerHTML = me
    ? (ability
        ? `<span class="ability-tag">🌟 توانایی شخصی — خط اول آمار شما</span>
           <span class="ability-text">«${escapeHtml(ability)}»</span>
           <div class="ability-bar"><div class="ability-fill"></div></div>`
        : `<span class="ability-tag">🌟 توانایی شخصی</span>
           <span class="ability-text">توانایی‌ات را بنویس — همیشه خط اول آمارت می‌شود.</span>`)
    : `<span class="ability-text">ابتدا از تب «اعضا» ثبت‌نام کن.</span>`;

  // Use community rating averages from the stat ratings system
  const ratings = state.statRatings || {};
  const avgs = ratings.averages || {};
  const myRatings = ratings.myRatings || {};
  const myAvgs = myRatings[me?.id] || {};
  const selfAvgs = avgs[me?.id] || {};

  $('#statsBars').innerHTML = STAT_KEYS.map(k => {
    const meta = STAT_META[k];
    // Show community average for this member, fall back to 0
    const v = selfAvgs[k] ?? myAvgs[k] ?? 0;
    const colorClass = { iq: '', comedy: 'gold', simp: 'pink', retardness: 'red' }[k] || '';
    return `<div class="bar-row">
      <div class="bar-label">${meta.emoji} ${meta.en}</div>
      <div class="bar-track"><div class="bar-fill ${colorClass}" data-fill="${v}" style="width:${v}%"></div></div>
      <div class="bar-value">${toFa(v)}٪ ${k === 'iq' ? '(امتیاز 되-sampled)' : ''}<small style="color:var(--muted);font-size:9px">(میانگین جامعه)</small></div>
    </div>`;
  }).join('');

  $('#abilityInput').value = ability || '';
  $('#abilityInput').disabled = !me;
  $('#saveAbilityBtn').disabled = !me;
  updateAbilityCounter();
  softField($('#abilityInput'));
}

const updateAbilityCounter = () => {
  const v = ($('#abilityInput') && $('#abilityInput').value) || '';
  const el = $('#abilityCounter');
  if (el) el.textContent = `${toFa(v.length)} / ۴۸`;
};

async function saveAbility() {
  const me = MY_MEMBER();
  if (!me) { $('#abilityNote').textContent = '❌ اول باید ثبت‌نام کنی.'; return; }
  const input = $('#abilityInput');
  const text = input.value.trim().replace(/\s+/g, ' ').slice(0, 48);
  if (text.length < 2) { $('#abilityNote').textContent = '❌ توانایی باید بین ۲ تا ۴۸ نویسه باشد.'; return; }
  $('#abilityNote').textContent = '⏳ در حال ثبت…';

  if (IN_TELEGRAM) {
    const res = await apiCall('/api/ability', { method: 'POST', body: JSON.stringify({ ability: text }) });
    if (!res) { $('#abilityNote').textContent = '❌ ثبت نشد — دوباره تلاش کن.'; return; }
    me.ability = text;
    setMeInList(me);
  } else {
    // حالت محلی بدون نیاز به سرور
    me.ability = text;
    saveLocalState(state);
  }
  haptic('light');
  $('#abilityNote').textContent = `🌟 ثبت شد: «${text}»`;
  renderStats();
  renderHome();
}

function setMeInList(me) {
  const i = MEMBERS.findIndex(x => x.id === me.id);
  if (i >= 0) MEMBERS[i] = { ...MEMBERS[i], ability: me.ability };
  saveLocalState(state);
}

/* ---------------------------------------------------------- رتبه‌بندی */
function renderRankings() {
  const rows = [...MEMBERS].sort((a, b) => finalOf(b) - finalOf(a));
  const medals = ['🥇', '🥈', '🥉'];
  $('#rankingList').innerHTML = rows.length === 0
    ? `<div class="empty-state">🏁 هنوز عضوی نداریم؛ یک نفر را از تب «اعضا» اضافه کن!</div>`
    : rows.slice(0, 10).map((m, i) => {
        const medal = medals[i] || toFa(i + 1) + '.';
        const f = finalOf(m), sign = f >= 0 ? '+' : '';
        return `<div class="rank-item ${m.id === ME_ID ? 'you' : ''}">
          <div class="rank-pos">${medal}</div>
          ${avatarHtml(m.name, m.id, 40)}
          <div class="rank-body">
            <div class="rank-name">${escapeHtml(m.name)}</div>
            <div class="rank-sentence">🟢 ${toFa(m.plus)} · 🟡 ${toFa(m.warn)} · 🔴 ${toFa(m.strike)} · ${STAT_KEYS.map(k => { const avg = mAvgs[k] ?? 0; const meta = STAT_META[k]; return `${meta.emoji} ${toFa(avg)}`; }).join(' · ')}</div>
          </div>
          <div class="rank-final ${f >= 0 ? 'plus' : 'minus'}">${sign}${toFa(f)}</div>
        </div>`;
      }).join('');

  const myPos = ME_ID ? viewRankOf(rows) : null;
  $('#rankingYou').textContent = ME_ID && myPos
    ? `📍 رتبه‌ی شما: ${toFa(myPos)} از ${toFa(rows.length)} نفر`
    : rows.length === 0 ? 'اول ثبت‌نام کن و رتبه بگیر!' : '';
}

/* ---------------------------------------------------------- خبر روز */
function renderNews() {
  const box = $('#newsBox');
  if (!box) return;
  const newsList = state.news || [];
  if (!Array.isArray(newsList) || newsList.length === 0) {
    box.innerHTML = `<div class="empty-state">📭 خبری ثبت نشده است.</div>`;
    return;
  }
  box.innerHTML = newsList.map(n => {
    let createdStr = '—';
    try {
      const d = new Date(n.created_at || 0);
      if (!isNaN(d.getTime())) {
        createdStr = d.toLocaleString('fa-IR', { year: 'numeric', month: 'long', day: 'numeric', hour: '2-digit', minute: '2-digit' });
      }
    } catch (e) { /* ok */ }
    return `<div class="news-card">
      <div class="news-head">
        <span class="news-tag">📢 خبر</span>
        <span class="news-by">توسط ${escapeHtml(n.author_name || 'ناشناس')}</span>
      </div>
      <div class="news-body">${escapeHtml(n.content)}</div>
      <div class="news-foot">🗓 ${createdStr}</div>
    </div>`;
  }).join('');
}

/* ---------------------------------------------------------- رأی‌گیری استرایک */
async function voteStrike(vid, vote) {
  if (!IN_TELEGRAM) { showToast('❌ این قابلیت فقط داخل تلگرام کار می‌کند.'); return; }
  const res = await apiCall('/api/strike-vote-vote', { method: 'POST', body: JSON.stringify({ vid, vote }) });
  if (res && res.ok) {
    showToast('✅ رأی شما ثبت شد.');
    await fetchStrikeVotes();
    renderStrikeVotes();
  } else {
    showToast('❌ رأی ثبت نشد — دوباره تلاش کن.');
  }
}

async function closeStrikeVote(vid) {
  if (!IN_TELEGRAM) { showToast('❌ این قابلیت فقط داخل تلگرام کار می‌کند.'); return; }
  const res = await apiCall('/api/strike-vote-close', { method: 'POST', body: JSON.stringify({ vid }) });
  if (res && res.ok) {
    showToast('🏁 رأی‌گیری بسته شد.');
    await fetchStrikeVotes();
    renderStrikeVotes();
    await refreshState();
    renderAll();
  } else {
    showToast('❌ بستن ناموفق — فقط پیشنهاددهنده می‌تواند ببندد.');
  }
}

function renderStrikeVotes() {
  const box = $('#strikeVoteBox');
  if (!box) return;
  const votes = state.strikeVotes || [];
  // Show/hide strike actions card based on admin status
  const actionsCard = $('#strikeActionsCard');
  if (actionsCard) actionsCard.style.display = isAdmin ? 'block' : 'none';
  if (votes.length === 0) {
    box.innerHTML = `<div class="empty-state">⚖️ هیچ رأی‌گیری استرایک بازی وجود ندارد.</div>`;
    return;
  }
  box.innerHTML = votes.map(v => {
    const strikes = v.strikes || 0;
    const clears = v.clears || 0;
    const voters = v.voters || 0;
    const isProposer = ME_ID && v.proposer_id === ME_ID;
    let expiresStr = '—';
    try {
      const exp = new Date(v.closes_at || 0);
      if (!isNaN(exp.getTime())) {
        expiresStr = exp.toLocaleString('fa-IR', { year: 'numeric', month: 'long', day: 'numeric', hour: '2-digit', minute: '2-digit' });
      }
    } catch (e) { /* ok */ }
    const closeBtn = isProposer ? `<button class="btn primary strike-close-btn" data-vid="${v.id}">🏁 اعلام نتیجه</button>` : '';
    return `<div class="strike-card">
      <div class="strike-head">
        <span class="strike-tag">🔴 استرایک</span>
        <span class="strike-by">توسط ${escapeHtml(v.proposer_name || 'ادمین')}</span>
      </div>
      <div class="strike-target">⚖️ «${escapeHtml(v.target_name || '—')}»</div>
      ${v.reason ? `<div class="strike-reason">💬 ${escapeHtml(v.reason)}</div>` : ''}
      <div class="strike-stats">
        <span class="strike-stat strike">🔴 استرایک: ${toFa(strikes)}</span>
        <span class="strike-stat clear">🟢 پاک کردن: ${toFa(clears)}</span>
        <span class="strike-stat">👥 ${toFa(voters)}</span>
      </div>
      <div class="strike-vote-btns">
        <button class="btn strike-vote-btn" data-vid="${v.id}" data-vote="strike">🔴 استرایک</button>
        <button class="btn strike-clear-btn" data-vid="${v.id}" data-vote="clear">🟢 پاک کردن</button>
        ${closeBtn}
      </div>
      <div class="strike-foot">⏳ تا ${expiresStr}</div>
    </div>`;
  }).join('');

  box.querySelectorAll('.strike-vote-btn, .strike-clear-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const vid = parseInt(btn.dataset.vid);
      const vote = btn.dataset.vote;
      haptic('light');
      voteStrike(vid, vote);
    });
  });
  box.querySelectorAll('.strike-close-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const vid = parseInt(btn.dataset.vid);
      haptic('medium');
      closeStrikeVote(vid);
    });
  });
}

/* ---------------------------------------------------------- اقدامات استرایک */
function populateStrikeTargetSelect() {
  const sel = $('#strikeTargetSelect');
  if (!sel) return;
  const current = sel.value;
  sel.innerHTML = '<option value="">-- انتخاب عضو --</option>';
  [...MEMBERS].sort((a, b) => a.name.localeCompare(b.name)).forEach(m => {
    const opt = document.createElement('option');
    opt.value = m.id;
    opt.textContent = m.name + (m.username ? ' (@' + m.username + ')' : '');
    sel.appendChild(opt);
  });
  if (current) sel.value = current;
}

async function startStrikeVote() {
  const sel = $('#strikeTargetSelect');
  const reasonInput = $('#strikeReasonInput');
  const note = $('#strikeActionNote');
  const targetId = parseInt(sel?.value);
  if (!targetId) { if (note) note.textContent = '❌ ابتدا یک عضو را انتخاب کن.'; return; }
  const target = MEMBERS.find(m => m.id === targetId);
  if (!target) { if (note) note.textContent = '❌ عضو یافت نشد.'; return; }
  const reason = (reasonInput?.value || '').trim() || null;
  if (note) note.textContent = '⏳ در حال شروع رأی‌گیری…';
  const res = await apiCall('/api/strike-vote-start', { method: 'POST', body: JSON.stringify({ target_id: targetId, target_name: target.name, reason }) });
  if (res && res.ok) {
    if (note) note.textContent = '✅ رأی‌گیری شروع شد.';
    haptic('light');
    if (reasonInput) reasonInput.value = '';
    await fetchStrikeVotes();
    renderStrikeVotes();
  } else {
    if (note) note.textContent = '❌ شروع ناموفق — ' + (res?.error || 'دوباره تلاش کن.');
  }
}

async function removeStrike() {
  const sel = $('#strikeTargetSelect');
  const note = $('#strikeActionNote');
  const targetId = parseInt(sel?.value);
  if (!targetId) { if (note) note.textContent = '❌ ابتدا یک عضو را انتخاب کن.'; return; }
  const target = MEMBERS.find(m => m.id === targetId);
  if (!target) { if (note) note.textContent = '❌ عضو یافت نشد.'; return; }
  const reason = ($('#strikeReasonInput')?.value || '').trim() || null;
  if (note) note.textContent = '⏳ در حال حذف استرایک…';
  const res = await apiCall('/api/unstrike', { method: 'POST', body: JSON.stringify({ target_id: targetId, reason }) });
  if (res && res.ok) {
    if (note) note.textContent = '🟢 یک استرایک حذف شد.';
    haptic('light');
    await refreshState();
    renderAll();
  } else {
    if (note) note.textContent = '❌ حذف ناموفق — ' + (res?.error || 'فقط ادمین اصلی می‌تواند حذف کند.');
  }
}

async function clearStrikes() {
  const sel = $('#strikeTargetSelect');
  const note = $('#strikeActionNote');
  const targetId = parseInt(sel?.value);
  if (!targetId) { if (note) note.textContent = '❌ ابتدا یک عضو را انتخاب کن.'; return; }
  const target = MEMBERS.find(m => m.id === targetId);
  if (!target) { if (note) note.textContent = '❌ عضو یافت نشد.'; return; }
  if (!confirm('همه‌ی استرایک‌های «' + target.name + '» پاک شوند؟')) return;
  const reason = ($('#strikeReasonInput')?.value || '').trim() || null;
  if (note) note.textContent = '⏳ در حال پاک کردن…';
  const res = await apiCall('/api/clearstrikes', { method: 'POST', body: JSON.stringify({ target_id: targetId, reason }) });
  if (res && res.ok) {
    if (note) note.textContent = '🧹 همه‌ی استرایک‌ها پاک شد.';
    haptic('light');
    await refreshState();
    renderAll();
  } else {
    if (note) note.textContent = '❌ پاک کردن ناموفق — ' + (res?.error || 'فقط ادمین اصلی می‌تواند پاک کند.');
  }
}

async function clearStrikes() {
  const sel = $('#strikeTargetSelect');
  const note = $('#strikeActionNote');
  const targetId = parseInt(sel?.value);
  if (!targetId) { if (note) note.textContent = '❌ ابتدا یک عضو را انتخاب کن.'; return; }
  const target = MEMBERS.find(m => m.id === targetId);
  if (!target) { if (note) note.textContent = '❌ عضو یافت نشد.'; return; }
  if (!confirm('همه‌ی استرایک‌های «' + target.name + '» پاک شوند؟')) return;
  const reason = ($('#strikeReasonInput')?.value || '').trim() || null;
  if (note) note.textContent = '⏳ در حال پاک کردن…';
  const res = await apiCall('/api/clearstrikes', { method: 'POST', body: JSON.stringify({ target_id: targetId, reason }) });
  if (res && res.ok) {
    if (note) note.textContent = '🧹 همه‌ی استرایک‌ها پاک شد.';
    haptic('light');
    await refreshState();
    renderAll();
  } else {
    if (note) note.textContent = '❌ پاک کردن ناموفق — ' + (res?.error || 'فقط ادمین اصلی می‌تواند پاک کند.');
  }
}

/* ---------------------------------------------------------- امتیازدهی آمار */
async function fetchStatRatings() {
  if (!IN_TELEGRAM) {
    return state.statRatings || {};
  }
  try {
    const res = await apiCall('/api/stat-ratings');
    if (res) {
      state.statRatings = res;
      saveLocalState(state);
      return res;
    }
    return state.statRatings || {};
  } catch (e) {
    return state.statRatings || {};
  }
}

async function submitStatRating(targetId, stat, value) {
  if (!IN_TELEGRAM) { showToast('❌ این قابلیت فقط داخل تلگرام کار می‌کند.'); return false; }
  const res = await apiCall('/api/rate-stat', { method: 'POST', body: JSON.stringify({ target_id: targetId, stat, value }) });
  if (res && res.ok) {
    showToast('✅ امتیاز ثبت شد.');
    haptic('light');
    await fetchStatRatings();
    renderRate();
    return true;
  } else {
    showToast('❌ ثبت ناموفق — ' + (res?.error || 'دوباره تلاش کن.'));
    return false;
  }
}

function renderRate() {
  const box = $('#rateBox');
  if (!box) return;
  const ratings = state.statRatings || {};
  const avgs = ratings.averages || {};
  const myRatings = ratings.myRatings || {};
  const me = MY_MEMBER();
  if (MEMBERS.length === 0) {
    box.innerHTML = `<div class="empty-state">👥 هنوز عضوی ثبت نشده است.</div>`;
    return;
  }
  box.innerHTML = MEMBERS.map(m => {
    const mAvgs = avgs[m.id] || {};
    const myR = myRatings[m.id] || {};
    const isMe = m.id === ME_ID;
    return `<div class="rate-card ${isMe ? 'rate-me' : ''}">
      <div class="rate-head">
        ${avatarHtml(m.name, m.id, 36)}
        <div class="rate-name">${escapeHtml(m.name)}</div>
        ${isMe ? '<span class="you-badge">(شما)</span>' : ''}
      </div>
      <div class="rate-stats">
        ${STAT_KEYS.map(k => {
          const meta = STAT_META[k];
          const avg = mAvgs[k]?.avg ?? '—';
          const cnt = mAvgs[k]?.count ?? 0;
          const myVal = myR[k] ?? '';
          const selfInput = isMe
      ? `<input type="number" min="0" max="100" class="rate-input" data-target="${m.id}" data-stat="iq" placeholder="امتیاز خود را وارد کن" />`
      : '';
          return `<div class="rate-row">
            <span class="rate-label">${meta.emoji} ${meta.en}</span>
            ${isMe && k === 'iq' ? selfInput : ''}
            <span class="rate-avg">میانگین: ${toFa(avg)} (${toFa(cnt)})</span>
          </div>`;
        }).join('')}
      </div>
    </div>`;
  }).join('');

  box.querySelectorAll('.rate-input').forEach(input => {
    input.addEventListener('change', async () => {
      const targetId = parseInt(input.dataset.target);
      const stat = input.dataset.stat;
      const val = parseInt(input.value);
      if (isNaN(val) || val < 0 || val > 100) {
        input.value = '';
        showToast('❌ امتیاز باید بین ۰ تا ۱۰۰ باشد.');
        return;
      }
      haptic('light');
      await submitStatRating(targetId, stat, val);
    });
  });
}

/* ---------------------------------------------------------- رندر همه */
function renderAll() {
  renderHome();
  renderStats();
  renderRankings();
  renderNews();
  renderStrikeVotes();
  renderRate();
  populateStrikeTargetSelect();
}

function bindEvents() {
  $$('.nav-btn').forEach(btn => btn.addEventListener('click', () => { haptic('light'); showScreen(btn.dataset.screen); }));
  $$('[data-nav]').forEach(btn => btn.addEventListener('click', () => showScreen(btn.dataset.nav)));
  // Segmented control removed - single ranking view
  $('#saveAbilityBtn').addEventListener('click', saveAbility);
  $('#abilityInput').addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); saveAbility(); }
  });
  $('#abilityInput').addEventListener('input', updateAbilityCounter, { passive: true });

  // Strike action buttons
  const startBtn = $('#startStrikeVoteBtn');
  if (startBtn) startBtn.addEventListener('click', () => { haptic('medium'); startStrikeVote(); });
  const removeBtn = $('#removeStrikeBtn');
  if (removeBtn) removeBtn.addEventListener('click', () => { haptic('light'); removeStrike(); });
  const clearBtn = $('#clearStrikesBtn');
  if (clearBtn) clearBtn.addEventListener('click', () => { haptic('medium'); clearStrikes(); });

  // News post button (event delegation for reliability)
  document.addEventListener('click', async (e) => {
    if (e.target && e.target.id === 'postNewsBtn') {
      const input = document.getElementById('newsContentInput');
      const note = document.getElementById('newsPostNote');
      const content = (input?.value || '').trim();
      if (content.length < 2) { if (note) note.textContent = '❌ متن خبر خیلی کوتاه است.'; return; }
      if (note) note.textContent = '⏳ در حال ثبت…';
      const ok = await postNews(content);
      if (ok) {
        if (input) input.value = '';
        if (note) note.textContent = '✅ خبر ثبت شد.';
      } else {
        if (note) note.textContent = '❌ ثبت ناموفق — دوباره تلاش کن.';
      }
    }
  });
}

function safeRenderAll() {
  try { renderAll(); } catch (e) { console.error('render failed:', e); }
}

async function init() {
  try {
    tg?.ready?.();
    tg?.expand?.();
    const tp = tg?.themeParams || {};
    if (tp.bg_color) document.documentElement.style.setProperty('--tg-theme-bg-color', tp.bg_color);
    if (tp.secondary_bg_color) document.documentElement.style.setProperty('--tg-theme-secondary-bg-color', tp.secondary_bg_color);
    if (tp.text_color) document.documentElement.style.setProperty('--tg-theme-text-color', tp.text_color);
    if (tp.hint_color) document.documentElement.style.setProperty('--tg-theme-hint-color', tp.hint_color);
    if (tp.button_color) document.documentElement.style.setProperty('--tg-theme-button-color', tp.button_color);
    if (tp.button_text_color) document.documentElement.style.setProperty('--tg-theme-button-text-color', tp.button_text_color);
  } catch (e) { /* rendering خارج از تلگرام هم باید کار کند */ }

  // دکمه‌ها را اول از همه روشن کن — حتی اگر سرور قطع باشد، باید کار کنند
  bindEvents();

  // دیتابیس واقعی از سرور مشترک ربات اصلی خوانده می‌شود
  let online = false;
  try {
    online = await refreshState();
  } catch (e) {
    console.error('refreshState failed:', e);
    online = false;
  }
  const bar = document.getElementById('offlineBar');
  if (bar) bar.style.display = IN_TELEGRAM && !online ? 'block' : 'none';

  // خبر روز را از سرور ربات اصلی بگیر
  try { await fetchNews(); } catch (e) { console.error('fetchNews failed:', e); }

  // رأی‌گیری‌های استرایک فعال را از سرور بگیر
  try { await fetchStrikeVotes(); } catch (e) { console.error('fetchStrikeVotes failed:', e); }

  try { await fetchStatRatings(); } catch (e) { console.error('fetchStatRatings failed:', e); }

  updateTopBar();
  safeRenderAll();
}

document.addEventListener('DOMContentLoaded', init);
window.addEventListener('error', e => console.error('app error:', e.message));
