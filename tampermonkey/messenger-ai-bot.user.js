// ==UserScript==
// @name         Shahidulla Kaysar Messenger AI Bot
// @namespace    https://github.com/Mouno6969/Bot
// @version      1.0.0
// @description  Mention-based Messenger assistant with /image, /voice, /sing, and /edit commands.
// @match        https://www.facebook.com/messages/*
// @match        https://www.messenger.com/*
// @grant        GM_getValue
// @grant        GM_setValue
// @grant        GM_xmlhttpRequest
// @connect       api.manus.im
// @connect       *.manuscdn.com
// @connect       *
// ==/UserScript==

(() => {
  'use strict';

  const CONFIG = {
    botName: 'Shahidulla Kaysar',
    apiBase: 'https://api.manus.im',
    pollMs: 3000,
    monitorMs: 2000,
    mediaTimeoutMs: 240000,
    contextChars: 6000,
    mentionWindowChars: 1400,
    keyName: 'shahidulla_manus_api_key',
  };

  let lastPageText = '';
  let busy = false;
  let lastSent = '';
  const seen = new Set();

  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const fingerprint = (text) => `${text.length}:${text.slice(-180)}:${text.slice(0, 80)}`;
  const normalized = (text) => (text || '').replace(/\u200b/g, ' ').replace(/\s+/g, ' ').trim();

  function notify(message, error = false) {
    let box = document.getElementById('sk-bot-status');
    if (!box) {
      box = document.createElement('div');
      box.id = 'sk-bot-status';
      box.style.cssText = [
        'position:fixed', 'right:16px', 'bottom:16px', 'z-index:2147483647',
        'max-width:320px', 'padding:10px 12px', 'border-radius:8px',
        'font:13px/1.35 system-ui,sans-serif', 'color:#fff',
        'box-shadow:0 4px 18px rgba(0,0,0,.28)'
      ].join(';');
      document.documentElement.appendChild(box);
    }
    box.style.background = error ? '#b42318' : '#155eef';
    box.textContent = `Shahidulla Bot: ${message}`;
    clearTimeout(notify.timer);
    notify.timer = setTimeout(() => box.remove(), 6500);
  }

  function getApiKey() {
    let key = GM_getValue(CONFIG.keyName, '');
    if (!key) {
      key = window.prompt('Paste your Manus API key. It will be stored only in Tampermonkey local storage on this browser.');
      if (key) GM_setValue(CONFIG.keyName, key.trim());
    }
    return (key || '').trim();
  }

  function request({ method, url, headers = {}, data, responseType = 'text' }) {
    return new Promise((resolve, reject) => {
      GM_xmlhttpRequest({
        method,
        url,
        headers,
        data,
        responseType,
        timeout: 120000,
        onload: (response) => {
          if (response.status >= 200 && response.status < 300) resolve(response);
          else reject(new Error(`HTTP ${response.status}: ${response.responseText || response.statusText}`));
        },
        onerror: () => reject(new Error('Network request failed.')),
        ontimeout: () => reject(new Error('Network request timed out.')),
      });
    });
  }

  async function apiJson(method, path, body) {
    const key = getApiKey();
    if (!key) throw new Error('No Manus API key was entered.');
    const response = await request({
      method,
      url: `${CONFIG.apiBase}${path}`,
      headers: {
        'x-manus-api-key': key,
        'Content-Type': 'application/json',
      },
      data: body ? JSON.stringify(body) : undefined,
    });
    const json = JSON.parse(response.responseText);
    if (!json.ok) throw new Error(json.error?.message || 'Manus API request failed.');
    return json;
  }

  function hasMention(text) {
    const lowered = text.toLocaleLowerCase();
    return lowered.includes(CONFIG.botName.toLocaleLowerCase()) || lowered.includes('@shahidulla');
  }

  function parseCommand(text) {
    const match = text.match(/\/(image|voice|sing|edit|help)\b\s*([\s\S]*)/i);
    if (!match) return { kind: 'chat', argument: '' };
    return { kind: match[1].toLocaleLowerCase(), argument: normalized(match[2]) };
  }

  function commandHelp() {
    return 'Commands: /image <description>, /voice <text>, /sing <brief or lyrics>, and /edit <instruction> with an image attached in the same message. For normal questions, just mention me.';
  }

  function findComposer() {
    const selectors = [
      "div[role='textbox'][contenteditable='true']",
      "[role='textbox'][contenteditable='true']",
      "div[contenteditable='true']",
    ];
    for (const selector of selectors) {
      const candidates = [...document.querySelectorAll(selector)].filter((element) => element.offsetParent !== null);
      if (candidates.length) return candidates[candidates.length - 1];
    }
    return null;
  }

  async function waitForComposer(timeout = 20000) {
    const deadline = Date.now() + timeout;
    while (Date.now() < deadline) {
      const composer = findComposer();
      if (composer) return composer;
      await sleep(500);
    }
    throw new Error('Messenger composer was not found. Refresh the chat and try again.');
  }

  async function sendText(text) {
    const composer = await waitForComposer();
    composer.focus();
    document.execCommand('selectAll', false, null);
    document.execCommand('insertText', false, text);
    composer.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: text }));
    composer.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, key: 'Enter', code: 'Enter' }));
    composer.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true, key: 'Enter', code: 'Enter' }));
    lastSent = text;
    await sleep(1200);
  }

  async function taskCreate(content, title) {
    const created = await apiJson('POST', '/v2/task.create', {
      message: { content },
      agent_profile: 'manus-1.6-lite',
      interactive_mode: false,
      hide_in_task_list: true,
      share_visibility: 'private',
      title,
    });
    return created.task_id;
  }

  async function listMessages(taskId) {
    return apiJson('GET', `/v2/task.listMessages?task_id=${encodeURIComponent(taskId)}`);
  }

  function latestStatus(data) {
    return (data.messages || []).find((message) => message.type === 'status_update')?.status_update?.agent_status || '';
  }

  async function waitForText(taskId) {
    const deadline = Date.now() + 120000;
    while (Date.now() < deadline) {
      await sleep(CONFIG.pollMs);
      const data = await listMessages(taskId);
      if (latestStatus(data) !== 'stopped') continue;
      const message = (data.messages || []).find((item) => item.type === 'assistant_message' && item.assistant_message?.content?.trim());
      if (message) return message.assistant_message.content.trim();
      break;
    }
    throw new Error('The reply task did not return text in time.');
  }

  function mediaType(attachment) {
    const type = String(attachment.type || '').toLowerCase();
    const contentType = String(attachment.content_type || '').toLowerCase();
    if (type === 'image' || contentType.startsWith('image/')) return 'image';
    if (['audio', 'music', 'voice'].includes(type) || contentType.startsWith('audio/')) return 'audio';
    return 'file';
  }

  async function waitForMedia(taskId, expectedType) {
    const deadline = Date.now() + CONFIG.mediaTimeoutMs;
    while (Date.now() < deadline) {
      await sleep(4000);
      const data = await listMessages(taskId);
      for (const message of data.messages || []) {
        if (message.type !== 'assistant_message') continue;
        for (const attachment of message.assistant_message?.attachments || []) {
          if (attachment.url && mediaType(attachment) === expectedType) return attachment;
        }
      }
      if (latestStatus(data) === 'stopped') break;
    }
    throw new Error(`The ${expectedType} job finished without a usable attachment.`);
  }

  async function attachmentToFile(attachment) {
    const response = await request({ method: 'GET', url: attachment.url, responseType: 'blob' });
    const type = attachment.content_type || (mediaType(attachment) === 'image' ? 'image/png' : 'audio/mpeg');
    return new File([response.response], attachment.filename || `generated.${mediaType(attachment) === 'image' ? 'png' : 'mp3'}`, { type });
  }

  async function sendFile(file) {
    let input = [...document.querySelectorAll("input[type='file']")].find((element) => element.offsetParent !== null) || document.querySelector("input[type='file']");
    if (!input) {
      const attach = document.querySelector("[aria-label='Attach a file'], [aria-label*='Attach']");
      if (!attach) throw new Error('Messenger file attachment control was not found.');
      attach.click();
      await sleep(800);
      input = document.querySelector("input[type='file']");
    }
    if (!input) throw new Error('Messenger file chooser did not open.');

    const transfer = new DataTransfer();
    transfer.items.add(file);
    input.files = transfer.files;
    input.dispatchEvent(new Event('change', { bubbles: true }));
    await sleep(2500);
    const composer = await waitForComposer();
    composer.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, key: 'Enter', code: 'Enter' }));
    composer.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true, key: 'Enter', code: 'Enter' }));
    lastSent = `[file] ${file.name}`;
  }

  async function uploadSourceImage(file) {
    const created = await apiJson('POST', '/v2/file.upload', { filename: file.name });
    await request({
      method: 'PUT',
      url: created.upload_url,
      headers: { 'Content-Type': file.type || 'application/octet-stream' },
      data: file,
    });
    return created.file.id;
  }

  async function recentChatImage() {
    const candidates = [...document.images].reverse();
    for (const image of candidates) {
      const width = image.naturalWidth || image.width || 0;
      const height = image.naturalHeight || image.height || 0;
      const label = `${image.alt || ''} ${image.getAttribute('aria-label') || ''}`.toLowerCase();
      if (!image.currentSrc?.startsWith('http') || width < 160 || height < 160) continue;
      if (label.includes('profile') || label.includes('avatar')) continue;
      try {
        const response = await request({ method: 'GET', url: image.currentSrc, responseType: 'blob' });
        return new File([response.response], 'messenger_edit_source.png', { type: response.response.type || 'image/png' });
      } catch (_) { /* Try the next image candidate. */ }
    }
    return null;
  }

  function mediaPrompt(kind, instruction) {
    const common = 'Return exactly one generated attachment and no explanatory prose. Do not ask follow-up questions; make reasonable assumptions.';
    if (kind === 'image') return `Generate exactly one high-quality image based on: ${instruction}\n\n${common}`;
    if (kind === 'voice') return `Generate exactly one natural TTS audio file. Speak in Bengali, Banglish, English, or mixed pronunciation to match this exact script. Delivery instructions are in English before the colon; spoken script follows: Speak warmly, clearly, and conversationally at a natural pace: ${instruction}\n\n${common}`;
    if (kind === 'sing') return `Create exactly one original song audio file based on: ${instruction}\n\nKeep it under 90 seconds unless a shorter duration is specified. Use original lyrics and do not imitate a living artist or reproduce copyrighted lyrics. ${common}`;
    throw new Error(`Unsupported media command: ${kind}`);
  }

  async function normalReply(context) {
    const prompt = `You are ${CONFIG.botName}, replying inside a Messenger group.\n\nRecent visible conversation:\n---\n${context}\n---\n\nReply only to the newest question or request addressed to you. Match the user's language and script exactly: Bengali, Banglish, or English. Be concise and natural. For comparisons about fluency, manners, skills, or behavior, make claims only when the visible conversation provides direct support. If context is insufficient, say so instead of inventing facts. Do not generate media in normal chat; tell users to use /image, /voice, /sing, or /edit. Output only the message to post.`;
    return waitForText(await taskCreate(prompt, 'Messenger mention reply'));
  }

  async function handleMedia(kind, argument) {
    if (!argument) {
      await sendText(`Please add details after /${kind}. Try /${kind} <your request>.`);
      return;
    }
    await sendText({ image: 'Image ta banachhi—ektu wait koro.', voice: 'Voice note ta toiri korchhi—ektu wait koro.', sing: 'Original song ta banachhi—ektu wait koro.', edit: 'Image ta edit korchhi—ektu wait koro.' }[kind]);

    let content = mediaPrompt(kind, argument);
    if (kind === 'edit') {
      const source = await recentChatImage();
      if (!source) {
        await sendText('For /edit, attach one image in the same message and add the edit instruction after /edit.');
        return;
      }
      const fileId = await uploadSourceImage(source);
      content = [
        { type: 'text', text: `Edit the attached image according to: ${argument}\n\nChange only the requested elements. Preserve identity, pose, lighting, background, perspective, and non-target details. Return exactly one edited image and no prose.` },
        { type: 'file', file_id: fileId },
      ];
    }
    const taskId = await taskCreate(content, `Messenger ${kind} command`);
    const attachment = await waitForMedia(taskId, kind === 'image' || kind === 'edit' ? 'image' : 'audio');
    await sendFile(await attachmentToFile(attachment));
  }

  async function handleIncoming(pageText, freshText) {
    const request = parseCommand(freshText);
    if (request.kind === 'help') return sendText(commandHelp());
    if (request.kind === 'chat') return sendText(await normalReply(pageText.slice(-CONFIG.contextChars)));
    return handleMedia(request.kind, request.argument);
  }

  async function monitor() {
    const current = document.body?.innerText || '';
    if (!lastPageText) {
      lastPageText = current;
      notify('Active. Existing messages are ignored; waiting for a fresh mention.');
      return;
    }
    if (current === lastPageText || busy) return;
    const fresh = current.startsWith(lastPageText) ? current.slice(lastPageText.length) : current.slice(-500);
    lastPageText = current;
    if (!fresh || !hasMention(fresh)) return;
    if (lastSent && fresh.includes(lastSent)) return;

    const id = fingerprint(fresh);
    if (seen.has(id)) return;
    seen.add(id);
    if (seen.size > 80) seen.delete(seen.values().next().value);

    busy = true;
    try {
      notify('Mention detected. Processing request…');
      await handleIncoming(current, fresh);
      notify('Request completed.');
    } catch (error) {
      console.error('[Shahidulla Bot]', error);
      notify(error.message || 'Request failed.', true);
      try { await sendText(`Sorry, request ta complete korte parlam na: ${error.message || 'unknown error'}`); } catch (_) { /* Show the local toast if Messenger itself is unavailable. */ }
    } finally {
      busy = false;
    }
  }

  setInterval(() => { monitor().catch((error) => console.error('[Shahidulla Bot monitor]', error)); }, CONFIG.monitorMs);
  window.addEventListener('beforeunload', () => { lastPageText = ''; });
})();
