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

/* ---------------------------------------------------------- وضعیت (اتصال زنده به دیتابیس ربات اصلی) */
const API_BASE = (window.BBH_API_BASE || '').replace(/\/+$/, '');
const tgUser = tg?.initDataUnsafe?.user || null;

const apiCall = async (path, opts = {}) => {
  if (!API_BASE) return null;
  try {
    const res = await fetch(API_BASE + path, {
      ...opts,
      headers: {
        'Content-Type': 'application/json',
        'X-Init-Data': tg?.initData || '',
        ...(opts.headers || {}),
      },
    });
    if (!res.ok) return null;
    return await res.json();
  } catch (e) { return null; }
};

/* اعضا از دیتابیس زنده‌ی ربات اصلی می‌آیند؛ در صورت قطع سرور، لیست خالی نمایش داده می‌شود */
const state = { members: [], meId: null, judgments: [] };

const refreshState = async () => {
  const data = await apiCall('/api/state');
  if (!data) return false;
  state.members.length = 0;
  for (const m of (data.members || [])) {
    state.members.push({
      id: m.id,
      username: m.username || null,
      name: m.name,
      age: m.age ?? null,
      ability: m.ability || '',
      plus: m.plus || 0,
      warn: m.warn || 0,
      strike: m.strike || 0,
      stats: m.stats || { iq: 0, comedy: 0, simp: 0, retardness: 0 },
      final: m.final ?? 0,
      createdAt: new Date().toISOString(),
    });
  }
  state.judgments = Array.isArray(data.history) ? data.history : [];
  return true;
};

const MEMBERS = state.members;
const ME_ID = tgUser?.id || null;
const MY_MEMBER = () => MEMBERS().find(m => m.id === ME_ID) || null;

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

  const myStats = me?.stats || { iq: 0, comedy: 0, simp: 0, retardness: 0 };
  $('#statsBars').innerHTML = STAT_KEYS.map(k => {
    const meta = STAT_META[k];
    const v = myStats[k] ?? 0;
    const colorClass = { iq: '', comedy: 'gold', simp: 'pink', retardness: 'red' }[k] || '';
    return `<div class="bar-row">
      <div class="bar-label">${meta.emoji} ${meta.en}</div>
      <div class="bar-track"><div class="bar-fill ${colorClass}" data-fill="${v}" style="width:${v}%"></div></div>
      <div class="bar-value">${toFa(v)}٪</div>
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

function saveAbility() {
  const me = MY_MEMBER();
  if (!me) { $('#abilityNote').textContent = '❌ اول باید ثبت‌نام کنی.'; return; }
  const input = $('#abilityInput');
  const text = input.value.trim().replace(/\s+/g, ' ').slice(0, 48);
  if (text.length < 2) { $('#abilityNote').textContent = '❌ توانایی باید بین ۲ تا ۴۸ نویسه باشد.'; return; }
  $('#abilityNote').textContent = '⏳ در حال ثبت…';
  apiCall('/api/ability', {
    method: 'POST',
    body: JSON.stringify({ ability: text }),
  }).then(res => {
    if (!res) { $('#abilityNote').textContent = '❌ اتصال سرور قطع شد؛ دوباره تلاش کن.'; return; }
    me.ability = text;
    haptic('light');
    $('#abilityNote').textContent = `🌟 ثبت شد: «${text}»`;
    renderStats();
    renderHome();
  });
}

/* ---------------------------------------------------------- رتبه‌بندی */
let rankTab = 'points';

function renderRankings() {
  const rows = [...MEMBERS].sort((a, b) => finalOf(b) - finalOf(a));
  const medals = ['🥇', '🥈', '🥉'];
  $('#rankingList').innerHTML = rows.length === 0
    ? `<div class="empty-state">🏁 هنوز عضوی نداریم؛ یک نفر را از تب «اعضا» اضافه کن!</div>`
    : rows.slice(0, 10).map((m, i) => {
        const medal = medals[i] || toFa(i + 1) + '.';
        if (rankTab === 'points') {
          const f = finalOf(m), sign = f >= 0 ? '+' : '';
          return `<div class="rank-item ${m.id === ME_ID ? 'you' : ''}">
            <div class="rank-pos">${medal}</div>
            ${avatarHtml(m.name, m.id, 40)}
            <div class="rank-body">
              <div class="rank-name">${escapeHtml(m.name)}</div>
              <div class="rank-sub">🟢 ${toFa(m.plus)} · 🟡 ${toFa(m.warn)} · 🔴 ${toFa(m.strike)}</div>
            </div>
            <div class="rank-final ${f >= 0 ? 'plus' : 'minus'}">${sign}${toFa(f)}</div>
          </div>`;
        }
        const avg = STAT_KEYS.reduce((t, k) => t + (m.stats?.[k] || 0), 0) / STAT_KEYS.length;
        return `<div class="rank-item ${m.id === ME_ID ? 'you' : ''}">
          <div class="rank-pos">${medal}</div>
          ${avatarHtml(m.name, m.id, 40)}
          <div class="rank-body">
            <div class="rank-name">${escapeHtml(m.name)}</div>
            <div class="rank-sub">⭐ میانگین: ${toFa(Math.round(avg))}٪</div>
          </div>
          <div class="rank-final plus">${toFa(Math.round(avg))}٪</div>
        </div>`;
      }).join('');

  const myPos = ME_ID ? viewRankOf(rows) : null;
  $('#rankingYou').textContent = ME_ID && myPos
    ? `📍 رتبه‌ی شما: ${toFa(myPos)} از ${toFa(rows.length)} نفر`
    : rows.length === 0 ? 'اول ثبت‌نام کن و رتبه بگیر!' : '';
}

/* ---------------------------------------------------------- دادگاه */
function renderCourt() {
  const open = state.judgments.filter(j => j.status !== 'closed');
  const history = state.judgments.filter(j => j.status === 'closed');

  $('#courtOpen').innerHTML = open.length === 0
    ? `<div class="empty-state">⚖️ هیچ پرونده‌ی بازی وجود ندارد.<br/>این بخش بعد از ثبت اولین شکایت فعال می‌شود.</div>`
    : open.map(j => `
      <div class="case">
        <div class="case-head">
          <div class="case-target">⚖️ ${escapeHtml(j.target_name || '—')}</div>
          <span class="verdict-badge strike">در جریان</span>
        </div>
        <div class="case-reason">💬 ${escapeHtml(j.reason || '—')} · 👮 ${escapeHtml(j.admin_name || '—')}</div>
      </div>
    `).join('');

  $('#courtHistory').innerHTML = history.length === 0
    ? `<div class="empty-state">📜 هیچ تاریخچه‌ی دادگاهی ثبت نشده است.</div>`
    : history.map(j => {
        const won = j.winner === 'strike' ? '🔨 استرایک' : (j.winner === 'chance' ? '🟩 فرصت دیگر' : '—');
        const when = escapeHtml((j.created_at || '').slice(0, 10) || '—');
        return `<div class="history-item">
          <span>${escapeHtml(j.target_name || '—')} <small style="color:var(--muted)">(${when})</small></span>
          <span class="history-winner" style="color:${j.winner === 'strike' ? '#d6403c' : '#2f8f5b'}">${won}</span>
        </div>`;
      }).join('');
}

/* ---------------------------------------------------------- اعضا + افزودن عضو */
function renderMembers() {
  $('#memberCount').textContent = toFa(MEMBERS.length) + ' نفر';
  const signupHtml = `
    <div class="signup-box">
      <div class="signup-title">✍️ ثبت‌نام در برادری BBH</div>
      <div class="signup-sub">نام را بنویس — آمار و امتیاز شما از صفر شروع می‌شود.</div>
      <div class="ability-row">
        <textarea id="signupName" class="soft-field" maxlength="60" rows="1" placeholder="نام و نام‌خانوادگی" autocomplete="off"></textarea>
        <button class="btn primary" id="signupMeBtn">شروع با خودم</button>
      </div>
      <div class="signup-hint">اعضای جدید از خودشان ثبت‌نام می‌کنند؛ افزودنِ دیگران فقط با دستور /addmember در ربات اصلی انجام می‌شود.</div>
    </div>`;

  const avatar = (name, id, size) => {
    const ch = (name || '؟').charAt(0);
    return `<div class="rank-avatar" style="width:${size}px;height:${size}px;background:${colorFor(id)};font-size:${Math.floor(size / 2)}px">${ch}</div>`;
  };
  const fmtAge = age => age != null ? `<span class="member-age">${toFa(age)} سال</span>` : '';
  const fmtAbility = a => a ? `<span class="member-ability">🌟 ${escapeHtml(a)}</span>` : '';

  $('#membersList').innerHTML = MEMBERS.length === 0
    ? signupHtml + `<div class="empty-state">👥 هنوز هیچ عضوی اضافه نکرده‌ای — اولین نفر باش!</div>`
    : signupHtml + [...MEMBERS].sort((a, b) => finalOf(b) - finalOf(a)).map(m => {
        const f = finalOf(m), sign = f >= 0 ? '+' : '';
        const mine = m.id === ME_ID ? ' <span class="you-badge">(شما)</span>' : '';
        const userTag = m.username ? `<span class="member-user">@${escapeHtml(m.username)}</span>` : '';
        return `<div class="member-row">
          ${avatar(m.name, m.id, 40)}
          <div class="member-body">
            <div class="member-name">${escapeHtml(m.name)}${mine} ${userTag}</div>
            <div class="member-sub">${fmtAge(m.age)} ${fmtAbility(m.ability)}</div>
          </div>
          <div class="member-final ${f >= 0 ? 'plus' : 'minus'}">${sign}${toFa(f)}</div>
        </div>`;
      }).join('');

  const meBtn = $('#signupMeBtn');
  if (meBtn) meBtn.addEventListener('click', () => doSignup(true));
  const input = $('#signupName');
  if (input) {
    input.addEventListener('keydown', e => {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); doSignup(true); }
    });
    softField(input);
  }
}

async function doSignup(asSelf = true) {
  if (!asSelf) { showToast('❌ افزودنِ دیگران از اپ انجام نمی‌شود؛ با دستور /addmember در ربات اصلی عضو می‌شوند.'); return; }
  const input = $('#signupName');
  const name = (input?.value || '').trim().split('\n')[0].slice(0, 60);
  if (name.length < 2) { showToast('❌ نام باید حداقل ۲ نویسه باشد'); return; }
  showToast('⏳ در حال ثبت‌نام…');
  const res = await apiCall('/api/register', { method: 'POST', body: JSON.stringify({ name }) });
  if (!res) { showToast('❌ اتصال سرور قطع شد؛ دوباره تلاش کن.'); return; }
  haptic('heavy');
  showToast('🎉 ثبت‌نام شما انجام شد!');
  await refreshState();
  renderAll();
}

/* ---------------------------------------------------------- رندر همه */
function renderAll() {
  renderHome();
  renderStats();
  renderRankings();
  renderCourt();
  renderMembers();
}

/* ---------------------------------------------------------- راه‌اندازی ------------------------------------------------------ */
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
  } catch (e) { /* اجرا در خارج از تلگرام هم ممکن است */ }

  // دیتابیس واقعی از سرور مشترک ربات اصلی خوانده می‌شود
  const online = await refreshState();
  const bar = document.getElementById('offlineBar');
  if (bar) bar.style.display = online ? 'none' : 'block';
  updateTopBar();
  renderAll();

  $$('.nav-btn').forEach(btn => btn.addEventListener('click', () => { haptic('light'); showScreen(btn.dataset.screen); }));
  $$('[data-nav]').forEach(btn => btn.addEventListener('click', () => showScreen(btn.dataset.nav)));

  $$('.seg[data-rank-tab]').forEach(btn => btn.addEventListener('click', () => {
    rankTab = btn.dataset.rankTab;
    $$('.seg[data-rank-tab]').forEach(b => b.classList.toggle('active', b === btn));
    renderRankings();
    haptic('light');
  }));

  $('#saveAbilityBtn').addEventListener('click', saveAbility);
  $('#abilityInput').addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); saveAbility(); }
  });
  $('#abilityInput').addEventListener('input', updateAbilityCounter, { passive: true });
}

document.addEventListener('DOMContentLoaded', init);
