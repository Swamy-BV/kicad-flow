// The GUI and CLI share one session. Credentials remain in the Codex process.
const $ = id => document.getElementById(id);
let state = {}, token = '', revision = '', posting = false, polling = false;
let messagesKey = '', questionsKey = '', modelKey = '';
const node = (tag, text = '', className = '') => {
  const element = document.createElement(tag);
  element.textContent = text;
  element.className = className;
  return element;
};
function render() {
  const busy = Boolean(state.busy || posting), account = state.account;
  document.body.classList.toggle('linked', Boolean(account && state.connected));
  $('account-summary').textContent = account && state.connected ? 'Codex linked' : 'Link agent';
  $('project-name').textContent = state.project_name || 'Your next design';
  if (document.activeElement !== $('project')) $('project').value = state.project || '';
  $('open-project').disabled = busy;
  $('new-chat').disabled = busy || !state.project;
  $('send').hidden = Boolean(state.busy);
  $('send').disabled = busy || !state.project || !account || !state.connected;
  $('stop').hidden = !state.busy;
  $('stop').disabled = posting;
  $('mode').disabled = busy;
  $('phase').textContent = state.phase || 'Select a project and link your agent';
  $('error').textContent = state.error || '';
  $('error').hidden = !state.error;
  $('account-error').textContent = state.error || '';
  $('account-error').hidden = !state.error;
  $('account-label').textContent = account && state.connected
    ? [account.email || (account.type === 'apiKey' ? 'API key account' : 'Connected account'), account.planType].filter(Boolean).join(' · ')
    : state.installed ? 'Use an account already signed in on this computer, or sign in below.' : 'Install Codex CLI, then reopen this connection.';
  $('connect').textContent = state.connected ? 'Refresh local account' : 'Use local account';
  for (const id of ['connect', 'login', 'device-login']) $(id).disabled = busy || !state.installed || Boolean(state.login?.auth_url);
  $('disconnect').hidden = !state.connected;
  $('disconnect').disabled = busy;
  const login = state.login || {};
  $('login-pending').hidden = !login.auth_url;
  if (login.auth_url) {
    const url = new URL(login.auth_url);
    if (url.protocol === 'https:' && ['auth.openai.com', 'chatgpt.com'].includes(url.hostname)) $('login-link').href = url.href;
    else $('login-link').removeAttribute('href');
  }
  $('device-code').textContent = login.user_code || '';
  $('device-code').hidden = !login.user_code;
  const options = state.models || [];
  const nextModels = JSON.stringify(options);
  if (nextModels !== modelKey) {
    $('model').replaceChildren(...options.map(m => {
      const option = node('option', m.displayName || m.model);
      option.value = m.model;
      return option;
    }));
    if (!options.length) $('model').append(node('option', 'Connect to load models'));
    $('model').value = state.model || '';
    modelKey = nextModels;
  }
  $('model').disabled = busy || !options.length;
  const nextMessages = JSON.stringify([state.project, state.messages || []]);
  if (messagesKey !== nextMessages) {
    const area = $('messages');
    const follow = area.scrollHeight - area.scrollTop - area.clientHeight < 90;
    area.replaceChildren(...(state.messages || []).map(message => {
      const article = node('article', '', 'message ' + message.role);
      article.append(node('strong', message.role === 'user' ? 'YOU' : 'KICADFLOW'), node('p', message.text));
      return article;
    }));
    if (!state.messages?.length) {
      const welcome = node('div', '', 'welcome');
      const steps = node('ol');
      for (const text of ['Open your project folder.', 'Link your Codex account.', 'Describe a change and inspect the result.']) steps.append(node('li', text));
      welcome.append(node('span', '✧', 'symbol'), node('h2', state.project ? 'What are we building?' : 'Start with your project.'), node('p', 'Work on your schematic and PCB, with the design and tool activity beside the conversation.'), steps);
      area.append(welcome);
    }
    if (follow) area.scrollTop = area.scrollHeight;
    messagesKey = nextMessages;
  }
  const entries = state.activity || [];
  $('tool-count').textContent = entries.length ? String(entries.length) : '';
  $('tools').replaceChildren(...entries.map(entry => node('li', entry.tool + ' · ' + entry.status, entry.status)));
  const usage = state.usage || {};
  $('usage').textContent = Number.isFinite(usage.totalTokens)
    ? 'Reported tokens: ' + usage.totalTokens.toLocaleString() + (Number.isFinite(usage.cachedInputTokens) ? ' · cached input: ' + usage.cachedInputTokens.toLocaleString() : '')
    : 'Token usage appears when the agent reports it.';
  const pending = state.pending || [], nextQuestions = JSON.stringify(pending);
  $('questions').hidden = !pending.length;
  if (questionsKey !== nextQuestions) {
    $('questions').replaceChildren(...pending.map(question => {
      const form = node('form'), inputs = new Map();
      for (const q of question.params.questions || []) {
        const input = node('textarea');
        input.required = true;
        input.setAttribute('aria-label', q.header || q.question);
        const choices = (q.options || []).map(option => option.label).join(' / ');
        form.append(node('label', q.question + (choices ? '\n' + choices : '')), input);
        inputs.set(q.id, input);
      }
      form.append(node('button', 'Reply'));
      form.onsubmit = async event => {
        event.preventDefault();
        const answers = Object.fromEntries([...inputs].map(([id, input]) => [id, input.value]));
        await action('respond', {id: question.id, answers});
      };
      return form;
    }));
    questionsKey = nextQuestions;
  }
}
async function refresh(force = false) {
  const response = await fetch('/agent/state' + (force ? '' : '?since=' + encodeURIComponent(revision)));
  if (!response.ok) throw new Error('The local agent connection is unavailable');
  const data = await response.json();
  token = data.csrf;
  if (data.changed) {
    state = data; revision = String(data.revision);
    render();
  }
}
async function action(name, body = {}) {
  if (posting) return null;
  posting = true; render();
  try {
    if (!token) await refresh(true);
    const response = await fetch('/agent/' + name, {method:'POST', headers:{'Content-Type':'application/json', 'X-KiCadFlow-Chat':token}, body:JSON.stringify(body)});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Request failed');
    await refresh(true);
    return data;
  } catch (error) {
    state.error = error.message;
    return null;
  } finally { posting = false; render(); }
}
$('account-button').onclick = () => $('account-dialog').showModal();
$('close-account').onclick = () => $('account-dialog').close();
$('connect').onclick = () => action('connect');
$('login').onclick = () => action('login');
$('device-login').onclick = () => action('login', {device:true});
$('cancel-login').onclick = () => action('cancel-login');
$('disconnect').onclick = () => action('disconnect');
$('project-form').onsubmit = async event => {
  event.preventDefault();
  if (await action('project', {path:$('project').value})) $('preview').src = '/?embedded=1';
};
$('prompt-form').onsubmit = async event => {
  event.preventDefault();
  if (await action('send', {text:$('prompt').value, model:$('model').value, mode:$('mode').value})) $('prompt').value = '';
};
$('stop').onclick = () => action('stop');
$('new-chat').onclick = () => action('new');
async function poll() {
  if (polling) return;
  polling = true;
  try { await refresh(); }
  catch (error) { state.error = error.message; render(); }
  finally { polling = false; setTimeout(poll, document.hidden ? 3000 : 600); }
}
poll();
