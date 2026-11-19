const V='village-v18',PRE=['/offline','/static/style.css','/static/css/site.css','/static/app.js','/static/icons/icon-192.png'];
self.addEventListener('install',e=>{e.waitUntil(caches.open(V).then(c=>c.addAll(PRE)).then(()=>self.skipWaiting()))});
self.addEventListener('activate',e=>{e.waitUntil(caches.keys().then(k=>Promise.all(k.filter(x=>x!==V).map(x=>caches.delete(x)))).then(()=>self.clients.claim()))});
self.addEventListener('fetch',e=>{
  const r=e.request,u=new URL(r.url);
  if(r.method!=='GET'||u.origin!==location.origin)return;
  if(r.mode==='navigate'){e.respondWith(fetch(r).catch(()=>caches.match('/offline')));return}
  if(u.pathname.startsWith('/static/')){
    e.respondWith(caches.match(r).then(hit=>hit||fetch(r).then(res=>{if(res.ok){const cp=res.clone();caches.open(V).then(c=>c.put(r,cp))}return res})));
    return;
  }
  if(u.pathname.startsWith('/uploads/')){
    if(/\.(mp4|webm)$/i.test(u.pathname)||r.headers.has('Range')){e.respondWith(fetch(r));return}
    e.respondWith(caches.match(r).then(hit=>fetch(r).then(res=>{
      if(res.ok&&(res.headers.get('Cache-Control')||'').startsWith('public')){const cp=res.clone();caches.open(V).then(c=>c.put(r,cp))}
      return res;
    }).catch(()=>hit)));
  }
});
