(function(){
var TE=document.documentElement.lang==='te';
document.addEventListener('focusin',function(event){
 var field=event.target;
 if(field instanceof HTMLInputElement&&(!field.type||field.type==='text')&&!field.readOnly){
  field.setAttribute('lang','te');field.setAttribute('inputmode','text')
 }else if(field instanceof HTMLTextAreaElement){
  field.setAttribute('lang','te');field.setAttribute('inputmode','text')
 }
});
if('serviceWorker' in navigator){window.addEventListener('load',function(){navigator.serviceWorker.register('/sw.js').catch(function(){})})}
document.querySelectorAll('[data-share-url]').forEach(function(b){b.onclick=function(){
 var url=b.dataset.shareUrl,text=b.dataset.shareText||'';
 if(navigator.share){navigator.share({title:text,text:text,url:url}).catch(function(e){if(e.name!=='AbortError')window.open('https://wa.me/?text='+encodeURIComponent(text+'\n'+url),'_blank','noopener,noreferrer')})}
 else{window.open('https://wa.me/?text='+encodeURIComponent(text+'\n'+url),'_blank','noopener,noreferrer')}
}});
var viewer=document.getElementById('image-lightbox'),viewerImage=viewer&&viewer.querySelector('img'),lightboxOpener=null;
document.querySelectorAll('[data-lightbox-src]').forEach(function(b){b.onclick=function(){
 if(!viewer||!viewerImage)return;lightboxOpener=b;viewerImage.src=b.dataset.lightboxSrc;viewerImage.alt=b.dataset.lightboxAlt||b.getAttribute('aria-label')||'గ్రామ ఫోటో';viewer.hidden=false;
 var close=viewer.querySelector('.lightbox-close');if(close)close.focus()
}});
if(viewer){
 function closeViewer(){viewer.hidden=true;viewerImage.removeAttribute('src');if(lightboxOpener)lightboxOpener.focus()}
 viewer.addEventListener('click',function(e){if(e.target===viewer||e.target.closest('.lightbox-close'))closeViewer()});
 document.addEventListener('keydown',function(e){if(e.key==='Escape'&&!viewer.hidden)closeViewer()})
}
document.querySelectorAll('[data-youtube-id]').forEach(function(button){button.addEventListener('click',function(){
 var player=button.closest('[data-youtube-player]');if(!player)return;
 var frame=document.createElement('iframe');
 frame.src='https://www.youtube-nocookie.com/embed/'+encodeURIComponent(button.dataset.youtubeId)+'?autoplay=1';
 frame.title=button.dataset.videoTitle||'గ్రామ వీడియో';frame.loading='lazy';
 frame.allow='accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture';
 frame.allowFullscreen=true;frame.referrerPolicy='strict-origin-when-cross-origin';
 player.replaceChildren(frame)
})});
document.querySelectorAll('input[name="entry_id"]').forEach(function(input){input.addEventListener('change',function(){
 document.querySelectorAll('.vote-entry').forEach(function(card){card.classList.toggle('vote-entry-selected',card.contains(input)&&input.checked)})
})});
var deferred=null,btn=document.getElementById('installBtn');
window.addEventListener('beforeinstallprompt',function(e){e.preventDefault();deferred=e;if(btn){btn.hidden=false;var h=document.getElementById('installHint');if(h)h.hidden=true}});
if(btn)btn.onclick=function(){if(deferred){deferred.prompt();deferred=null;btn.hidden=true}};
document.querySelectorAll('[data-copy]').forEach(function(b){b.onclick=function(){var t=document.querySelector(b.dataset.copy);t.select();
 try{navigator.clipboard.writeText(t.value)}catch(e){document.execCommand('copy')}b.textContent=b.dataset.done||'OK'}});
document.querySelectorAll('form[data-confirm]').forEach(function(f){f.onsubmit=function(){return confirm(f.dataset.confirm)}});
function rel(iso){var s=(Date.now()-new Date(iso))/1000;if(isNaN(s))return iso;
 if(s<60)return TE?'ఇప్పుడే':'just now';if(s<3600)return Math.floor(s/60)+(TE?' నిమిషాల క్రితం':' min ago');
 if(s<86400)return Math.floor(s/3600)+(TE?' గంటల క్రితం':' h ago');return new Date(iso).toLocaleDateString(TE?'te-IN':undefined,{day:'numeric',month:'short',year:'numeric'})}
function stamp(){document.querySelectorAll('time[data-t]').forEach(function(t){t.textContent=rel(t.dataset.t)})}
stamp();setInterval(stamp,30000);
document.querySelectorAll('.chip[data-y]').forEach(function(c){c.onclick=function(){
 document.querySelectorAll('.chip[data-y]').forEach(function(x){x.classList.toggle('on',x===c)});
 document.querySelectorAll('.mosaic .tile').forEach(function(t){t.hidden=!(c.dataset.y==='all'||t.dataset.y===c.dataset.y)})}});
document.querySelectorAll('[data-program-editor]').forEach(function(editor){
 var rows=editor.querySelector('[data-program-rows]'),add=editor.querySelector('[data-add-program]'),template=document.getElementById('program-row-template');
 function update(){add.disabled=rows.children.length>=20}
 editor.addEventListener('click',function(e){
  if(e.target.closest('[data-add-program]')){if(rows.children.length<20)rows.append(template.content.cloneNode(true))}
  if(e.target.closest('[data-remove-program]'))e.target.closest('[data-program-row]').remove();
  update()
 });
 update()
});
document.querySelectorAll('[data-scroll-target]').forEach(function(button){
 var track=document.getElementById(button.dataset.scrollTarget),direction=Number(button.dataset.scrollDirection);
 if(!track||!direction)return;
 button.onclick=function(){track.scrollBy({left:track.clientWidth*.85*direction,behavior:'smooth'})};
 function update(){button.disabled=direction<0?track.scrollLeft<=1:track.scrollLeft+track.clientWidth>=track.scrollWidth-1}
 update();track.addEventListener('scroll',update,{passive:true});window.addEventListener('resize',update)
});
document.querySelectorAll('[data-home-slideshow]').forEach(function(slideshow){
 var main=slideshow.querySelector('[data-home-slide]'),thumbs=Array.from(slideshow.querySelectorAll('[data-home-photo]'));
 if(!main||!thumbs.length||window.matchMedia('(prefers-reduced-motion: reduce)').matches)return;
 var photos=[main.src].concat(thumbs.map(function(image){return image.src})),index=0;
 window.setInterval(function(){
  if(document.hidden)return;
  index=(index+1)%photos.length;
  main.classList.add('is-changing');
  window.setTimeout(function(){main.src=photos[index];main.classList.remove('is-changing')},220);
  thumbs.forEach(function(image,position){image.src=photos[(index+position+1)%photos.length]});
 },4500)
});
var feed=document.getElementById('feed');
if(feed&&feed.dataset.poll){setInterval(function(){fetch(feed.dataset.poll).then(function(r){return r.json()}).then(function(list){
 if(!list.length)return;feed.textContent='';
 list.forEach(function(u){var li=document.createElement('li'),h=document.createElement('div'),b=document.createElement('span'),t=document.createElement('time'),p=document.createElement('p');
  li.className='up cat-'+u.key+(u.pinned?' pin':'');h.className='uh';b.className='badge cat-'+u.key;b.textContent=u.cat;h.append(b);
  if(u.pinned){var pl=document.createElement('span');pl.className='pinlbl';pl.textContent=feed.dataset.pinned;h.append(pl)}
  t.dataset.t=u.t;h.append(t);p.textContent=u.text;li.append(h,p);
  if(u.photo){var im=document.createElement('img');im.className='upimg';im.src=u.photo;im.alt='';li.append(im)}
  var share=document.createElement('a');share.className='whatsapp-share';share.href=u.share;share.target='_blank';share.rel='noopener';share.textContent=feed.dataset.share;li.append(share);feed.append(li)});stamp()}).catch(function(){})},20000)}
})();
