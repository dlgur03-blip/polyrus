"""자체 완결 채팅 UI(HTML+CSS+JS 한 파일) — 빌드 스텝·외부 의존성 0. app.py가 그대로 서빙."""
from __future__ import annotations

INDEX_HTML = r"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Polyrus — 질문은 우리가, 당신은 대답만</title>
<style>
:root{--bg:#0e1116;--card:#171b22;--line:#2a313c;--fg:#e6e9ef;--mut:#8b95a5;--acc:#5b9dff;--ok:#3fb950;--warn:#d29922;--bad:#f85149}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Apple SD Gothic Neo",sans-serif}
.wrap{max-width:720px;margin:0 auto;min-height:100vh;display:flex;flex-direction:column}
header{padding:18px 20px;border-bottom:1px solid var(--line);display:flex;align-items:center;gap:12px}
header h1{font-size:16px;margin:0;font-weight:650}
header .sub{color:var(--mut);font-size:12.5px}
select,button,input{font:inherit;color:var(--fg)}
select{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:6px 10px;margin-left:auto}
#chat{flex:1;overflow-y:auto;padding:20px;display:flex;flex-direction:column;gap:14px}
.msg{max-width:80%;padding:11px 14px;border-radius:14px;white-space:pre-wrap;word-break:break-word}
.bot{background:var(--card);border:1px solid var(--line);border-top-left-radius:4px;align-self:flex-start}
.me{background:var(--acc);color:#06122a;border-top-right-radius:4px;align-self:flex-end;font-weight:550}
.tag{font-size:11px;color:var(--mut);margin:0 4px}
.chips{display:flex;flex-wrap:wrap;gap:8px;align-self:flex-start;max-width:85%}
.chip{background:#1e2530;border:1px solid var(--line);color:var(--fg);border-radius:999px;padding:6px 13px;cursor:pointer}
.chip:hover{border-color:var(--acc);color:var(--acc)}
form{display:flex;gap:8px;padding:14px 20px;border-top:1px solid var(--line)}
input{flex:1;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:11px 13px}
input:focus{outline:none;border-color:var(--acc)}
button.send{background:var(--acc);color:#06122a;border:none;border-radius:10px;padding:0 18px;font-weight:650;cursor:pointer}
button.send:disabled{opacity:.4;cursor:default}
.result{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px;align-self:stretch}
.result h3{margin:.1em 0 .5em}
.result pre{white-space:pre-wrap;font:inherit;margin:0;color:var(--fg)}
.vrow{display:flex;align-items:center;gap:8px;padding:4px 0;font-size:13.5px}
.dot{width:8px;height:8px;border-radius:50%;flex:none}
.pass{background:var(--ok)}.fail{background:var(--bad)}.inconclusive{background:var(--warn)}
.blockers{color:var(--warn);font-size:13.5px;margin-top:8px}
.start{align-self:center;background:var(--acc);color:#06122a;border:none;border-radius:10px;padding:11px 22px;font-weight:650;cursor:pointer;margin-top:8px}
.recovery{color:var(--warn)}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>Polyrus</h1><span class="sub">질문은 우리가, 당신은 대답만</span>
    <select id="domain"></select>
  </header>
  <div id="chat"></div>
  <form id="form" style="display:none">
    <input id="input" autocomplete="off" placeholder="여기에 답을 적어주세요…">
    <button class="send" type="submit">보내기</button>
  </form>
</div>
<script>
const chat=document.getElementById('chat'),form=document.getElementById('form'),input=document.getElementById('input'),sel=document.getElementById('domain');
let session=null;
function add(cls,text){const d=document.createElement('div');d.className='msg '+cls;d.textContent=text;chat.appendChild(d);chat.scrollTop=chat.scrollHeight;return d;}
function chips(opts){const c=document.createElement('div');c.className='chips';opts.forEach(o=>{const b=document.createElement('button');b.className='chip';b.textContent=o;b.onclick=()=>{input.value=o;form.requestSubmit();c.remove();};c.appendChild(b);});chat.appendChild(c);chat.scrollTop=chat.scrollHeight;}
function ask(q){const d=add('bot',(q.recovery?'↻ 그 답으론 진행이 어려워요. ':'')+q.prompt);if(q.recovery)d.classList.add('recovery');if(q.options&&q.options.length)chips(q.options);input.focus();}
async function start(){chat.innerHTML='';form.style.display='flex';
  const r=await fetch('/api/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({domain:sel.value})});
  const j=await r.json();session=j.session;if(j.question)ask(j.question);else done(j);}
async function send(text){add('me',text);
  const r=await fetch('/api/answer',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({session,answer:text})});
  const j=await r.json();if(j.question)ask(j.question);else done(j);}
function done(j){form.style.display='none';
  if(j.error){add('bot','⚠ '+j.error);return;}
  const R=j.result,box=document.createElement('div');box.className='result';
  let h='<h3>✅ 기획 완성 ('+R.domain+')</h3><pre>'+esc(R.brief)+'</pre>';
  if(R.verification&&R.verification.length){h+='<h3 style="margin-top:14px">검증</h3>';
    R.verification.forEach(v=>{h+='<div class="vrow"><span class="dot '+v.verdict+'"></span>'+esc(v.detail)+'</div>';});}
  if(R.blockers&&R.blockers.length){h+='<div class="blockers">⛔ '+R.blockers.map(esc).join('<br>')+'</div>';}
  h+='<button class="start" onclick="start()">다시 하기</button>';
  box.innerHTML=h;chat.appendChild(box);chat.scrollTop=chat.scrollHeight;}
function esc(s){return (s||'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}
form.onsubmit=e=>{e.preventDefault();const t=input.value.trim();if(!t)return;input.value='';send(t);};
(async()=>{const r=await fetch('/api/domains');const j=await r.json();
  j.domains.forEach(d=>{const o=document.createElement('option');o.value=d;o.textContent=d;sel.appendChild(o);});
  const s=document.createElement('button');s.className='start';s.textContent='시작하기';s.onclick=start;chat.appendChild(s);
})();
</script>
</body>
</html>"""
