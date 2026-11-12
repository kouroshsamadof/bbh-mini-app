/* AZIZ MINI — پنل برادری BBH (اپلیکیشن مجازی داخل تلگرام)
   همه‌ی متن‌ها فارسی، همه‌ی واکنش‌ها تعاملی، و حالت نمایشی با داده‌ی ساختگی بی‌خطر. */
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
  setTimeout(() => el.classList.remove('show'), 2200);
}

const haptic = kind => { try { tg?.HapticFeedback?.impactOccurred?.(kind || 'medium'); } catch (e) { /* noop */ } };

/* ---------------------------------------------------------- داده‌ی نمایشی (ساختگی — هیچ عضو واقعی این‌جا نیست) */
const MEMBERS = [
  { id: 1,  name: 'علی رضایی',  plus: 15, warn: 1, strike: 0, ability: 'کارتن درجه‌یک' },
  { id: 2,  name: 'مهدی کریمی', plus: 10, warn: 2, strike: 1, ability: 'سریع مثل صاعقه' },
  { id: 3,  name: 'رضا نجفی',   plus: 12, warn: 0, strike: 1, ability: 'باحال محله' },
  { id: 4,  name: 'حسین طاهری', plus: 7,  warn: 1, strike: 0, ability: '' },
  { id: 5,  name: 'محمد شریفی', plus: 9,  warn: 3, strike: 0, ability: '' },
  { id: 6,  name: 'امیر حسنی',  plus: 5,  warn: 2, strike: 2, ability: '' },
  { id: 7,  name: 'سعید یوسفی', plus: 4,  warn: 5, strike: 1, ability: '' },
  { id: 8,  name: 'کیوان مرادی',plus: 3,  warn: 1, strike: 2, ability: '' },
  { id: 9,  name: 'نیما جلیلی', plus: 2,  warn: 4, strike: 0, ability: '' },
  { id: 10, name: 'آرش عباسی',  plus: 8,  warn: 2, strike: 1, ability: '' },
  { id: 11, name: 'دانیال احمدی', plus: 6, warn: 0, strike: 0, ability: '' },
  { id: 12, name: 'پرهام موسوی', plus: 1, warn: 1, strike: 3, ability: '' },
];

/* آمار هر عضو — تولید قطعی از روی آیدی تا همیشه یکسان بماند */
function seededStats(id) {
  let seed = id * 2654435761 % 4294967296;
  const next = () => {
    seed = (seed * 1597334677 + 3812015801) % 4294967296;
    return seed;
  };
  return {
    iq: next() % 100,
    comedy: next() % 100,
    simp: next() % 100,
    retardness: next() % 100,
  };
}
const STAT_META = {
  iq:         { en: 'IQ',         emoji: '💡' },
  comedy:     { en: 'COMEDY',     emoji: '😂' },
  simp:       { en: 'SIMPNESS',   emoji: '🥰' },
  retardness: { en: 'RETARDNESS', emoji: '🐢' },
};
const STAT_ORDER = ['iq', 'comedy', 'simp', 'retardness'];

/* امتیاز نهایی با همان فرمول ربات اصلی: اول استرایک‌ها (هر ۵ پلاس)، سپس اخطارها (هر ۳ پلاس باقی‌مانده) */
function computeFinal(m) {
  const p = m.plus, w = m.warn, s = m.strike;
  const sErased = Math.min(s, Math.floor(p / 5));
  const rem = p - sErased * 5;
  const wErased = Math.min(w, Math.floor(rem / 3));
  const remainingNeg = (s - sErased) * 3 + (w - wErased) * 1;
  return { final: p - remainingNeg, sErased, wErased };
}
const MEMBER_STATS = {};
MEMBERS.forEach(m => { MEMBER_STATS[m.id] = seededStats(m.id); });
const MEMBER_FINAL = {};
MEMBERS.forEach(m => { MEMBER_FINAL[m.id] = computeFinal(m).final; });

/* پرونده‌های دادگاه — نمایشی */
const COURT_OPEN = [
  { id: 'c1', target: 'حسین طاهری', reason: 'توهین در چت گروهی', officer: 'علی رضایی', strike: 3, chance: 2, color: '#b45309' },
  { id: 'c2', target: 'کیوان مرادی', reason: 'تکرار حمله‌ی لفظی', officer: 'مهدی کریمی', strike: 2, chance: 4, color: '#1d4ed8' },
  { id: 'c3', target: 'نیما جلیلی',  reason: 'اسپم لینک تبلیغاتی', officer: 'علی رضایی', strike: 1, chance: 1, color: '#7c3aed' },
];
const COURT_HISTORY = [
  { target: 'سعید یوسفی', winner: 'strike', votes: '۴ — ۲', when: 'دیروز' },
  { target: 'رضا نجفی',   winner: 'chance', votes: '۱ — ۳', when: '۲ روز پیش' },
  { target: 'امیر حسنی',  winner: 'strike', votes: '۵ — ۰', when: '۴ روز پیش' },
  { target: 'آرش عباسی',  winner: 'chance', votes: '۲ — ۳', when: 'هفته‌ی گذشته' },
  { target: 'پرهام موسوی',winner: 'strike', votes: '۴ — ۱', when: 'هفته‌ی گذشته' },
];

/* ---------------------------------------------------------- وضعیت کاربر (localStorage) */
const STORAGE_KEY = 'bbh_mini_state_v1';
const state = (() => {
  try { return JSON.parse(localStorage.getItem(STORAGE_KEY)) || {}; }
  catch (e) { return {}; }
})();
if (!state.ability && MEMBERS[0].ability) state.ability = MEMBERS[0].ability;
if (!state.votes) state.votes = {};
if (!state.votesCast) state.votesCast = {};
const saveState = () => localStorage.setItem(STORAGE_KEY, JSON.stringify(state));

/* کاربر فعلی — اگر داخل تلگرام باز شود از TG استفاده می‌کنیم؛ وگرنه «علی رضایی» (نمایشی) */
const tgUser = tg?.initDataUnsafe?.user || null;
const ME = {
  id: MEMBERS[0].id,
  name: tgUser?.first_name ? `${tgUser.first_name}${tgUser.last_name ? ' ' + tgUser.last_name : ''}` : MEMBERS[0].name,
  short: tgUser?.first_name || MEMBERS[0].name.split(' ')[0],
  username: tgUser?.username || 'ali_rezaei',
};
if (tgUser?.id) ME.avatarChar = (tgUser.first_name || '؟').charAt(0);
const myMember = MEMBERS[0];
const myStats = MEMBER_STATS[myMember.id];

/* ---------------------------------------------------------- ابزارهای مشترک */
const AVATAR_COLORS = ['#2f8f5b', '#b45309', '#1d4ed8', '#7c3aed', '#db2777', '#0e7490', '#a16207', '#15803d'];
const colorFor = id => AVATAR_COLORS[id % AVATAR_COLORS.length];

function avatarHtml(name, id, size = 40) {
  const ch = (name || '؟').charAt(0);
  return `<div class="rank-avatar" style="width:${size}px;height:${size}px;background:${colorFor(id)};font-size:${Math.floor(size / 2)}px">${ch}</div>`;
}

function showScreen(name) {
  $$('.screen').forEach(s => s.classList.remove('active'));
  $('#screen-' + name).classList.add('active');
  $$('.nav-btn').forEach(b => b.classList.toggle('active', b.dataset.screen === name));
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

/* ---------------------------------------------------------- خانه */
function renderHome() {
  const c = computeFinal(myMember);
  const finalSign = c.final >= 0 ? '+' : '';
  $('#heroFinal').textContent = finalSign + toFa(c.final);
  $('#heroPlus').textContent = toFa(myMember.plus);
  $('#heroWarn').textContent = toFa(myMember.warn);
  $('#heroStrike').textContent = toFa(myMember.strike);

  const sorted = [...MEMBERS].sort((a, b) => MEMBER_FINAL[b.id] - MEMBER_FINAL[a.id]);
  const myPos = sorted.findIndex(m => m.id === myMember.id) + 1;
  $('#homeRankPreview').innerHTML = `
    <div class="rank-item you">
      <div class="rank-pos">${toFa(myPos)}</div>
      ${avatarHtml(ME.name, ME.id, 44)}
      <div class="rank-body">
        <div class="rank-name">${ME.name}</div>
        <div class="rank-sub">در جدول ${toFa(sorted.length)} نفری برادری</div>
      </div>
      <div class="rank-final ${c.final >= 0 ? 'plus' : 'minus'}">${finalSign}${toFa(c.final)}</div>
    </div>`;

  $('#homeStatsPreview').innerHTML = STAT_ORDER.map(k => {
    const meta = STAT_META[k], v = myStats[k];
    return `<div class="bar-row">
      <div class="bar-label">${meta.emoji} ${meta.en}</div>
      <div class="bar-track"><div class="bar-fill" data-fill="${v}"></div></div>
      <div class="bar-value">${toFa(v)}٪</div>
    </div>`;
  }).join('');
}

/* ---------------------------------------------------------- آمار */
function renderStats() {
  const nameEl = $('#statsName');
  if (nameEl) nameEl.textContent = ME.name;

  const ability = state.ability?.trim();
  $('#abilityBanner').innerHTML = ability ? `
    <span class="ability-tag">🌟 توانایی شخصی — خط اول آمار شما</span>
    <span class="ability-text">«${escapeHtml(ability)}»</span>
    <div class="ability-bar"><div class="ability-fill"></div></div>`
    : `<span class="ability-tag">🌟 توانایی شخصی</span>
       <span class="ability-text">شما هنوز توانایی‌تان را تعریف نکرده‌اید.</span>`;

  $('#statsBars').innerHTML = STAT_ORDER.map(k => {
    const meta = STAT_META[k], v = myStats[k];
    const colorClass = { iq: '', comedy: 'gold', simp: 'pink', retardness: 'red' }[k] || '';
    return `<div class="bar-row">
      <div class="bar-label">${meta.emoji} ${meta.en}</div>
      <div class="bar-track"><div class="bar-fill ${colorClass}" data-fill="${v}"></div></div>
      <div class="bar-value">${toFa(v)}٪</div>
    </div>`;
  }).join('');
  $('#abilityInput').value = ability || '';
  // انیمیشن پر شدن نوارها بعد از رندر
  requestAnimationFrame(() => setTimeout(animateBars, 60));
}

function animateBars() {
  requestAnimationFrame(() => {
    $$('.bar-fill[data-fill]').forEach(el => { el.style.width = el.dataset.fill + '%'; });
  });
}

function escapeHtml(str) {
  return String(str).replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
}

function saveAbility() {
  const input = $('#abilityInput');
  const text = input.value.trim().split('\n')[0].slice(0, 48);
  if (text.length < 2) { $('#abilityNote').textContent = '❌ «فقط» ۲ تا ۴۸ نویسه می‌نویسید.'; return; }
  state.ability = text;
  saveState();
  haptic('light');
  $('#abilityNote').textContent = `🌟 توانایی شما ثبت شد: «${text}»`;
  renderStats();
  renderHome();
}

/* ---------------------------------------------------------- رتبه‌بندی */
let rankTab = 'points';

function renderRankings() {
  const rows = [...MEMBERS].sort((a, b) => {
    if (rankTab === 'points') return MEMBER_FINAL[b.id] - MEMBER_FINAL[a.id];
    const sa = seededStats(a.id), sb = seededStats(b.id);
    const avgA = ['iq', 'comedy', 'simp', 'retardness'].reduce((t, k) => t + sa[k], 0) / 4;
    const avgB = ['iq', 'comedy', 'simp', 'retardness'].reduce((t, k) => t + sb[k], 0) / 4;
    return avgB - avgA;
  });
  const myPos = rows.findIndex(m => m.id === myMember.id) + 1;
  const medals = ['🥇', '🥈', '🥉'];

  $('#rankingList').innerHTML = rows.slice(0, 10).map((m, i) => {
    const medal = medals[i] || toFa(i + 1) + '.';
    if (rankTab === 'points') {
      const f = MEMBER_FINAL[m.id], sign = f >= 0 ? '+' : '';
      return `<div class="rank-item ${m.id === myMember.id ? 'you' : ''}">
        <div class="rank-pos">${medal}</div>
        ${avatarHtml(m.name, m.id, 40)}
        <div class="rank-body">
          <div class="rank-name">${m.name}</div>
          <div class="rank-sub">🟢 ${toFa(m.plus)} &nbsp;·&nbsp; 🟡 ${toFa(m.warn)} &nbsp;·&nbsp; 🔴 ${toFa(m.strike)}</div>
        </div>
        <div class="rank-final ${f >= 0 ? 'plus' : 'minus'}">${sign}${toFa(f)}</div>
      </div>`;
    }
    const st = MEMBER_STATS[m.id];
    const avg = Math.round(['iq', 'comedy', 'simp', 'retardness'].reduce((t, k) => t + st[k], 0) / 4);
    return `<div class="rank-item ${m.id === myMember.id ? 'you' : ''}">
      <div class="rank-pos">${medal}</div>
      ${avatarHtml(m.name, m.id, 40)}
      <div class="rank-body">
        <div class="rank-name">${m.name}</div>
        <div class="rank-sub">⭐ میانگین: ${toFa(avg)}٪</div>
      </div>
      <div class="rank-final plus">${toFa(avg)}٪</div>
    </div>`;
  }).join('');

  $('#rankingYou').textContent = `📍 رتبه‌ی شما: ${toFa(myPos)} از ${toFa(rows.length)} نفر`;
}

/* ---------------------------------------------------------- دادگاه */
function renderCourt() {
  $('#courtOpen').innerHTML = COURT_OPEN.map(c => {
    const myVote = state.votes[c.id];
    const strikeCount = c.strike + (myVote === 'strike' ? 1 : 0);
    const chanceCount = c.chance + (myVote === 'chance' ? 1 : 0);
    return `<div class="case" data-case="${c.id}">
      <div class="case-head">
        <div class="case-target">⚖️ ${c.target}</div>
        <span class="verdict-badge strike">در جریان</span>
      </div>
      <div class="case-reason">💬 ${c.reason} &nbsp;·&nbsp; 👮 ${c.officer}</div>
      <div class="case-votes">
        <div><div class="vote-count strike">${toFa(strikeCount)}</div><div class="vote-label">🟥 استرایک بزنید</div></div>
        <div><div class="vote-count chance">${toFa(chanceCount)}</div><div class="vote-label">🟩 یک فرصت دیگر</div></div>
      </div>
      <div class="vote-buttons">
        <button class="vote-btn strike ${myVote === 'strike' ? 'picked' : ''}" data-vote="strike" data-case="${c.id}">🟥 استرایک بزنید</button>
        <button class="vote-btn chance ${myVote === 'chance' ? 'picked' : ''}" data-vote="chance" data-case="${c.id}">🟩 فرصت دیگر</button>
      </div>
      <div class="case-footer"><span>⏱ رأی‌گیری ۵ دقیقه باز است</span><span>رای شما: ${myVote ? (myVote === 'strike' ? '🟥 استرایک' : '🟩 فرصت دیگر') : '—'}</span></div>
    </div>`;
  }).join('');

  $('#courtHistory').innerHTML = COURT_HISTORY.map(h => `
    <div class="history-item">
      <span>${h.target} <small style="color:var(--muted)">(${h.votes})</small></span>
      <span class="history-winner" style="color:${h.winner === 'strike' ? '#d6403c' : '#2f8f5b'}">${h.winner === 'strike' ? '🔨 استرایک' : '🟩 فرصت دیگر'} · ${h.when}</span>
    </div>`).join('');
}

function castVote(caseId, vote) {
  state.votes[caseId] = vote;
  saveState();
  haptic('medium');
  renderCourt();
  showToast(vote === 'strike' ? '🟥 رأی شما: استرایک ثبت شد' : '🟩 رأی شما: فرصت دیگر ثبت شد');
}

/* ---------------------------------------------------------- اعضا */
function renderMembers() {
  $('#memberCount').textContent = toFa(MEMBERS.length) + ' نفر';
  const sorted = [...MEMBERS].sort((a, b) => MEMBER_FINAL[b.id] - MEMBER_FINAL[a.id]);
  $('#membersList').innerHTML = sorted.map(m => {
    const f = MEMBER_FINAL[m.id];
    const sign = f >= 0 ? '+' : '';
    return `<div class="member-row">
      ${avatarHtml(m.name, m.id, 40)}
      <div class="member-name">${m.name}</div>
      <div class="member-final ${f >= 0 ? 'plus' : 'minus'}">${sign}${toFa(f)}</div>
    </div>`;
  }).join('');
}

/* ---------------------------------------------------------- راه‌اندازی ------------------------------------------------------ */
function init() {
  // تلگرام وب‌اپ — تم و ارتفاع کامل
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
  } catch (e) { /* در مرورگر خارج از تلگرام هم اجرا می‌شود */ }

  // هدر
  $('#topAvatar').textContent = (ME.avatarChar || ME.short || '؟').charAt(0);
  $('#topAvatar').style.background = '#f59e0b';
  $('#topName').textContent = ME.name;
  $('#topPlus').textContent = toFa(myMember.plus);

  const sorted = [...MEMBERS].sort((a, b) => MEMBER_FINAL[b.id] - MEMBER_FINAL[a.id]);
  $('#topRank').textContent = `رتبه‌ی شما: ${toFa(sorted.findIndex(m => m.id === myMember.id) + 1)}`;

  renderHome();
  renderStats();
  renderRankings();
  renderCourt();
  renderMembers();
  animateBars();

  // ناوبری
  $$('.nav-btn').forEach(btn => btn.addEventListener('click', () => { haptic('light'); showScreen(btn.dataset.screen); }));
  $$('[data-nav]').forEach(btn => btn.addEventListener('click', () => showScreen(btn.dataset.nav)));

  // رتبه‌بندی
  $$('.seg[data-rank-tab]').forEach(btn => btn.addEventListener('click', () => {
    rankTab = btn.dataset.rankTab;
    $$('.seg[data-rank-tab]').forEach(b => b.classList.toggle('active', b === btn));
    renderRankings();
    haptic('light');
  }));

  // توانایی
  $('#saveAbilityBtn').addEventListener('click', saveAbility);
  $('#abilityInput').addEventListener('keydown', e => { if (e.key === 'Enter') saveAbility(); });

  // آرای دادگاه (delegation)
  document.addEventListener('click', e => {
    const btn = e.target.closest('.vote-btn');
    if (!btn) return;
    castVote(btn.dataset.case, btn.dataset.vote);
  });
}

document.addEventListener('DOMContentLoaded', init);
