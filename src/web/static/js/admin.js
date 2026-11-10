/**
 * Standoff 2 Admin Panel — Minimalist Interaction & State Layer
 */

// --------------------------------------------------------------------------
// Toast Notifications
// --------------------------------------------------------------------------
function showToast(message, type = 'success') {
  const container = document.getElementById('toast-container');
  if (!container) return;

  const toast = document.createElement('div');
  toast.className = 'toast';
  const icon = type === 'success' ? 'fa-check' : 'fa-triangle-exclamation';
  toast.innerHTML = `<i class="fa-solid ${icon}"></i> <span>${message}</span>`;

  container.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transform = 'translateY(8px)';
    toast.style.transition = 'all 0.2s ease';
    setTimeout(() => toast.remove(), 200);
  }, 3500);
}

// --------------------------------------------------------------------------
// API Client Helper
// --------------------------------------------------------------------------
async function apiPost(url, data = {}) {
  try {
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
    const result = await res.json();
    if (!res.ok) {
      throw new Error(result.detail || 'Ошибка выполнения действия');
    }
    return result;
  } catch (err) {
    showToast(err.message, 'error');
    throw err;
  }
}

// --------------------------------------------------------------------------
// Administrative Actions (Monochrome & Direct)
// --------------------------------------------------------------------------
async function banUserCascade(userId, username = '') {
  const target = username ? `@${username} (${userId})` : `ID ${userId}`;
  if (!confirm(`Заблокировать пользователя ${target}?\n\nБудут деактивированы все объявления и закрыты активные чаты.`)) {
    return;
  }

  try {
    const res = await apiPost(`/admin/api/users/${userId}/ban`);
    showToast(`Пользователь ${userId} заблокирован. Лотов снято: ${res.ads_deactivated}`);
    setTimeout(() => location.reload(), 1000);
  } catch (e) {
    console.error(e);
  }
}

async function unbanUser(userId) {
  if (!confirm(`Разблокировать пользователя ID ${userId}?`)) return;

  try {
    await apiPost(`/admin/api/users/${userId}/unban`);
    showToast(`Пользователь ${userId} разблокирован`);
    setTimeout(() => location.reload(), 1000);
  } catch (e) {
    console.error(e);
  }
}

async function deleteUserPermanently(userId, username = '') {
  const target = username ? `@${username} (${userId})` : `ID ${userId}`;
  if (!confirm(`ВНИМАНИЕ! Полностью удалить пользователя ${target} из базы данных?\n\nБудут безвозвратно удалены все связанные объявления, чаты, сообщения и реферальные записи.\nПри следующем запуске бота пользователь будет считаться абсолютно новым!`)) {
    return;
  }

  try {
    await apiPost(`/admin/api/users/${userId}/delete`);
    showToast(`Пользователь ${userId} полностью удален из БД`);
    setTimeout(() => location.reload(), 1000);
  } catch (e) {
    console.error(e);
  }
}
window.deleteUserPermanently = deleteUserPermanently;

async function resolveReport(reportId) {
  try {
    await apiPost(`/admin/api/reports/${reportId}/resolve`);
    showToast(`Жалоба #${reportId} закрыта`);
    setTimeout(() => location.reload(), 800);
  } catch (e) {
    console.error(e);
  }
}

async function dismissReport(reportId) {
  try {
    await apiPost(`/admin/api/reports/${reportId}/dismiss`);
    showToast(`Жалоба #${reportId} отклонена`);
    setTimeout(() => location.reload(), 800);
  } catch (e) {
    console.error(e);
  }
}

async function deactivateAd(adId) {
  if (!confirm(`Снять объявление #${adId} с продажи?`)) return;

  try {
    await apiPost(`/admin/api/ads/${adId}/deactivate`);
    showToast(`Объявление #${adId} деактивировано`);
    setTimeout(() => location.reload(), 800);
  } catch (e) {
    console.error(e);
  }
}

async function activateAd(adId) {
  if (!confirm(`Восстановить и активировать объявление #${adId}?`)) return;

  try {
    await apiPost(`/admin/api/ads/${adId}/activate`);
    showToast(`Объявление #${adId} успешно активировано!`);
    setTimeout(() => location.reload(), 800);
  } catch (e) {
    console.error(e);
  }
}

// --------------------------------------------------------------------------
// Chat Log Modal & History Fetcher
// --------------------------------------------------------------------------
async function openChatModal(chatId, adTitle = '') {
  const modal = document.getElementById('chat-modal');
  const body = document.getElementById('chat-modal-body');
  const title = document.getElementById('chat-modal-title');
  if (!modal || !body) return;

  title.innerText = `Лог чата #${chatId} ${adTitle ? '• ' + adTitle : ''}`;
  body.innerHTML = `
    <div style="text-align: center; padding: 32px; color: var(--text-muted);">
      <i class="fa-solid fa-spinner fa-spin"></i>
      <p style="margin-top: 8px; font-size: 12px;">Загрузка сообщений...</p>
    </div>
  `;
  modal.classList.add('active');

  try {
    const res = await fetch(`/admin/api/chats/${chatId}/logs`);
    const logs = await res.json();

    if (!logs || logs.length === 0) {
      body.innerHTML = `<div style="text-align:center; padding:24px; color:var(--text-muted); font-size:12.5px;">Нет сообщений в данном чате.</div>`;
      return;
    }

    let html = '<div class="chat-log-container">';
    logs.forEach(msg => {
      const isSeller = msg.sender_role === 'seller';
      const roleClass = isSeller ? 'chat-seller' : 'chat-buyer';
      const roleTitle = isSeller ? 'Продавец' : 'Покупатель';
      const time = new Date(msg.sent_at).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });

      html += `
        <div class="chat-bubble ${roleClass}">
          <div class="chat-sender-tag">${roleTitle} (${msg.sender_id})</div>
          <div class="chat-text">${escapeHtml(msg.text_content || '[Медиафайл]')}</div>
          <div class="chat-time">${time}</div>
        </div>
      `;
    });
    html += '</div>';
    body.innerHTML = html;
  } catch (err) {
    body.innerHTML = `<div style="padding:16px; color:var(--btn-danger-text); font-size:12px;">Ошибка: ${err.message}</div>`;
  }
}

function closeChatModal() {
  const modal = document.getElementById('chat-modal');
  if (modal) modal.classList.remove('active');
}

function escapeHtml(text) {
  const div = document.createElement('div');
  div.innerText = text;
  return div.innerHTML;
}

function renderTelegramHtml(raw) {
  if (!raw) return '';
  // 1. Escape HTML entities
  let text = String(raw)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');

  // 2. Selectively unescape valid Telegram HTML tags
  text = text
    .replace(/&lt;(\/?)b&gt;/gi, '<$1b>')
    .replace(/&lt;(\/?)strong&gt;/gi, '<$1strong>')
    .replace(/&lt;(\/?)i&gt;/gi, '<$1i>')
    .replace(/&lt;(\/?)em&gt;/gi, '<$1em>')
    .replace(/&lt;(\/?)u&gt;/gi, '<$1u>')
    .replace(/&lt;(\/?)ins&gt;/gi, '<$1ins>')
    .replace(/&lt;(\/?)s&gt;/gi, '<$1s>')
    .replace(/&lt;(\/?)strike&gt;/gi, '<$1strike>')
    .replace(/&lt;(\/?)del&gt;/gi, '<$1del>')
    .replace(/&lt;(\/?)code&gt;/gi, '<$1code style="background:#27272a; padding:1px 4px; border-radius:3px; font-family:monospace; font-size:12px; color:#fca5a5;">')
    .replace(/&lt;(\/?)pre&gt;/gi, '<$1pre style="background:#27272a; padding:8px; border-radius:4px; font-family:monospace; font-size:12px; overflow-x:auto;">')
    .replace(/&lt;tg-spoiler&gt;/gi, '<span style="filter:blur(4px); cursor:pointer;" onclick="this.style.filter=\'none\'" title="Нажмите чтобы открыть спойлер">')
    .replace(/&lt;\/tg-spoiler&gt;/gi, '</span>')
    .replace(/&lt;blockquote&gt;/gi, '<blockquote style="border-left:3px solid #71717a; padding-left:10px; margin:6px 0; color:#a1a1aa;">')
    .replace(/&lt;\/blockquote&gt;/gi, '</blockquote>')
    .replace(/&lt;a\s+href=&quot;([^&"]+)&quot;&gt;(.*?)&lt;\/a&gt;/gi, function(match, url, linkText) {
      if (/^(https?:\/\/|tg:\/\/)/i.test(url)) {
        return `<a href="${url}" target="_blank" rel="noopener" style="color:#60a5fa; text-decoration:underline;">${linkText}</a>`;
      }
      return linkText;
    });

  return text.replace(/\n/g, '<br>');
}

// --------------------------------------------------------------------------
// Live UTC+3 Clock
// --------------------------------------------------------------------------
function initUtcClock() {
  const clockEl = document.getElementById('utc-clock');
  if (!clockEl) return;

  function updateClock() {
    const now = new Date();
    // UTC+3 offset in milliseconds (3 * 3600 * 1000)
    const utc3Time = new Date(now.getTime() + (now.getTimezoneOffset() * 60000) + (3 * 3600 * 1000));
    const h = String(utc3Time.getHours()).padStart(2, '0');
    const m = String(utc3Time.getMinutes()).padStart(2, '0');
    const s = String(utc3Time.getSeconds()).padStart(2, '0');
    clockEl.innerText = `${h}:${m}:${s} UTC+3`;
  }

  updateClock();
  setInterval(updateClock, 1000);
}

// --------------------------------------------------------------------------
// Responsive Sidebar & Collapse Toggle
// --------------------------------------------------------------------------
function initSidebar() {
  const toggleBtn = document.getElementById('sidebar-toggle');
  const sidebar = document.getElementById('sidebar');
  const backdrop = document.getElementById('sidebar-backdrop');
  if (!sidebar) return;

  const isMobile = () => window.innerWidth <= 900;

  // Restore collapsed state on desktop
  if (!isMobile() && localStorage.getItem('sidebar_collapsed') === 'true') {
    sidebar.classList.add('collapsed');
  }

  if (toggleBtn) {
    toggleBtn.addEventListener('click', () => {
      if (isMobile()) {
        sidebar.classList.toggle('mobile-open');
        if (backdrop) backdrop.classList.toggle('active');
      } else {
        sidebar.classList.toggle('collapsed');
        localStorage.setItem('sidebar_collapsed', sidebar.classList.contains('collapsed'));
      }
    });
  }

  if (backdrop) {
    backdrop.addEventListener('click', () => {
      sidebar.classList.remove('mobile-open');
      backdrop.classList.remove('active');
    });
  }

  // Handle window resizing
  window.addEventListener('resize', () => {
    if (!isMobile()) {
      sidebar.classList.remove('mobile-open');
      if (backdrop) backdrop.classList.remove('active');
      if (localStorage.getItem('sidebar_collapsed') === 'true') {
        sidebar.classList.add('collapsed');
      }
    }
  });
}

// --------------------------------------------------------------------------
// Lifecycle Initialization
// --------------------------------------------------------------------------
document.addEventListener('DOMContentLoaded', () => {
  initUtcClock();
  initSidebar();

  // Broadcast preview listener
  const broadcastInput = document.getElementById('broadcast-text');
  const broadcastPreview = document.getElementById('broadcast-preview');

  if (broadcastInput && broadcastPreview) {
    broadcastInput.addEventListener('input', () => {
      const val = broadcastInput.value.trim();
      if (!val) {
        broadcastPreview.innerHTML = '<span style="color:var(--text-muted); font-style:italic;">Текст сообщения появится здесь...</span>';
      } else {
        broadcastPreview.innerHTML = renderTelegramHtml(val);
      }
    });
  }

  // Close modal on Escape
  window.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeChatModal();
  });

  const modal = document.getElementById('chat-modal');
  if (modal) {
    modal.addEventListener('click', (e) => {
      if (e.target === modal) closeChatModal();
    });
  }
});
