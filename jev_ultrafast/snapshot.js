(() => {
  if (!document.body) return null;
  const cache = window.__jevFast ||= {ids:new WeakMap(), nodes:new Map(), next:1};
  cache.custom ||= new WeakSet();
  const identity = e => {
    if (!cache.ids.has(e)) cache.ids.set(e,cache.next++);
    const id=cache.ids.get(e); cache.nodes.set(id,e); return id;
  };
  for (const [id,e] of cache.nodes) if (!e.isConnected) cache.nodes.delete(id);
  const safe = e => !['password','file','hidden'].includes(e.type);
  const visible = e => !e.closest('[aria-hidden="true"],[inert]') &&
    e.checkVisibility({checkOpacity:true,checkVisibilityCSS:true});
  const name = (e,seen=new Set()) => {
    if (!e || seen.has(e)) return '';
    seen.add(e);
    const referenced=(e.getAttribute('aria-labelledby')||'').split(/\s+/)
      .map(id=>name(document.getElementById(id),seen)).filter(Boolean).join(' ');
    return referenced || e.getAttribute('aria-label') ||
      [...(e.labels||[])].map(l=>name(l,seen)).filter(Boolean).join(' ') ||
      (['button','submit','reset'].includes(e.type) ? e.value : '') || e.getAttribute('alt') ||
      (e.tagName==='INPUT' ? '' : [...e.childNodes].map(n=>n.nodeType===3 ? n.textContent :
        n.nodeType===1 && n.getAttribute('aria-hidden')!=='true' ? name(n,seen) : '').join(' ').trim()) ||
      e.getAttribute('title') || e.getAttribute('placeholder') || '';
  };
  const roles=['button','link','checkbox','radio','switch','tab','menuitem','menuitemradio',
    'option','gridcell','combobox','textbox','searchbox','spinbutton'];
  const selector='a[href],button,input,textarea,select,summary,[contenteditable="true"],'+
    roles.map(role=>'[role="'+role+'"]').join(',');
  const role = e => {
    const explicit=e.getAttribute('role');
    if (roles.includes(explicit)) return explicit;
    if (cache.custom.has(e)) return 'button';
    if (e.tagName==='BUTTON' || e.tagName==='SUMMARY') return 'button';
    if (e.tagName==='A') return 'link';
    if (e.tagName==='SELECT') return 'combobox';
    if (e.tagName==='TEXTAREA' || e.isContentEditable) return 'textbox';
    if (e.tagName==='INPUT') {
      if (['checkbox','radio'].includes(e.type)) return e.type;
      if (['button','submit','reset','image'].includes(e.type)) return 'button';
      if (e.type==='search') return 'searchbox';
      if (e.type==='number') return 'spinbutton';
      if (['text','email','url','tel'].includes(e.type)) return 'textbox';
    }
    return null;
  };
  cache.fieldState=root=>[...(root||document).querySelectorAll('input,textarea,select')].filter(safe)
    .map(e=>[identity(e),e.value,e.checked,e.selectedIndex,e.disabled,e.readOnly]);
  cache.pageKey=()=>[performance.timeOrigin,location.href,scrollX,scrollY,innerWidth,innerHeight];
  cache.guard=e=>{
    if (!e?.isConnected || !visible(e)) return null;
    const scope=e.closest('form,dialog,[role="dialog"],article,li,tr,[role="row"]') || e.parentElement;
    return [identity(e),role(e),name(e),e.value??null,e.checked??null,e.selectedIndex??null,
      e.readOnly??null,e.matches(':disabled'),e.getAttribute('aria-disabled'),
      e.getAttribute('aria-expanded'),e.getAttribute('aria-checked'),e.getAttribute('aria-selected'),
      e.getAttribute('href'),scope?.innerText?.slice(0,6000)||'',cache.fieldState(scope)];
  };
  const actions=[];
  for (const e of document.querySelectorAll(selector)) {
    if (!safe(e) || !visible(e) || e.matches(':disabled') || e.closest('[aria-disabled="true"]')) continue;
    const r=e.getBoundingClientRect(), x=r.x+r.width/2, y=r.y+r.height/2, rname=role(e);
    if (!rname || r.width<=0 || r.height<=0 || x<0 || y<0 || x>=innerWidth || y>=innerHeight) continue;
    if (rname==='gridcell' && e.querySelector('button,[role="button"]')) continue;
    const base={node:identity(e),role:rname,label:name(e)||rname,
      rect:{x:r.x,y:r.y,w:r.width,h:r.height}};
    if (e.tagName==='INPUT') Object.assign(base,{input_type:e.type,
      name:e.getAttribute('name')||'',placeholder:e.getAttribute('placeholder')||''});
    for (const key of ['checked','selected','expanded']) {
      const value=e.getAttribute('aria-'+key);
      if (value!==null) base[key]=value;
    }
    if (['checkbox','radio'].includes(e.type)) base.checked=String(e.checked);
    if (e.tagName==='SELECT') {
      for (const o of e.options) if (!o.selected && !o.disabled && !o.closest('optgroup[disabled]'))
        actions.push({...base,kind:'select',value:o.value,
          current_value:[...e.selectedOptions].map(o=>o.label).join(', '),label:base.label+' → '+o.label});
    } else {
      const editable=!e.readOnly && e.getAttribute('aria-readonly')!=='true' &&
        (['textbox','searchbox','spinbutton'].includes(rname) ||
          (rname==='combobox' && ['INPUT','TEXTAREA'].includes(e.tagName)));
      const value='value' in e ? String(e.value) :
        e.isContentEditable || rname==='combobox' ? e.innerText.trim() : '';
      actions.push({...base,kind:editable?'fill':'click',value});
      if (editable) {
        actions.push({...base,kind:'click',value,label:'Open '+base.label});
        if (e.tagName==='INPUT' && value.trim())
          actions.push({...base,kind:'submit',value,label:'Submit '+base.label});
      }
    }
  }
  // Modern apps often attach click behavior to plain elements through delegated
  // handlers. Admit a bounded set only when the element is visibly pointer-like,
  // has a useful observed label, and is not wrapping or wrapped by a semantic control.
  let customCount=0;
  const customSelector='[tabindex]:not([tabindex="-1"]),[onclick],div,span,li';
  for (const e of document.querySelectorAll(customSelector)) {
    if (customCount>=80) break;
    if (!safe(e) || !visible(e) || e.closest('[aria-disabled="true"],[inert]') ||
        e.matches(selector) || e.closest(selector) || e.querySelector(selector)) continue;
    const explicit=e.hasAttribute('onclick') ||
      (e.hasAttribute('tabindex') && e.getAttribute('tabindex')!=='-1');
    const pointer=getComputedStyle(e).cursor==='pointer';
    if (!explicit && !pointer) continue;
    if (!explicit && e.parentElement && e.parentElement!==document.body &&
        getComputedStyle(e.parentElement).cursor==='pointer') continue;
    const r=e.getBoundingClientRect(), x=r.x+r.width/2, y=r.y+r.height/2;
    if (!r.width || !r.height || x<0 || y<0 || x>=innerWidth || y>=innerHeight) continue;
    const hit=document.elementFromPoint(x,y);
    if (!hit || (hit!==e && !e.contains(hit))) continue;
    const label=name(e).replace(/\s+/g,' ').trim().slice(0,240);
    if (!label) continue;
    cache.custom.add(e); customCount++;
    actions.push({node:identity(e),role:'button',label,kind:'click',value:'',
      rect:{x:r.x,y:r.y,w:r.width,h:r.height}});
  }
  const words=[], walker=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);
  const range=document.createRange(); let node,length=0;
  while ((node=walker.nextNode()) && length<6000) {
    const value=node.textContent.trim(), parent=node.parentElement;
    if (!value || !parent || parent.closest('script,style,noscript,template') || !visible(parent)) continue;
    range.selectNodeContents(node); const r=range.getBoundingClientRect();
    if (r.width>0 && r.height>0 && r.bottom>0 && r.top<innerHeight && r.right>0 && r.left<innerWidth) {
      words.push(value); length+=value.length;
    }
  }
  const text=words.join('\n').slice(0,6000), height=document.documentElement.scrollHeight;
  // Read-only list context: rendered-row counts and a small look-ahead, never
  // new action targets. Keep arbitrary offscreen article bodies/footers out.
  const rowSelector='li,article,tr,[role="listitem"],[role="row"]';
  const listSelector='ul,ol,table,[role="list"],[role="table"],[role="grid"]';
  const excluded='nav,header,footer,aside,[role="navigation"],[role="menu"],'+
    '[role="menubar"],[role="listbox"]';
  const candidates=new Set(document.querySelectorAll(listSelector));
  for (const row of document.querySelectorAll(rowSelector))
    if (!row.closest(listSelector)) candidates.add(row.parentElement);
  const groups=[]; let omittedGroups=0, previewBudget=2400;
  const rowText=e=>{
    const out=[], reader=document.createTreeWalker(e,NodeFilter.SHOW_TEXT);
    let n,size=0;
    while ((n=reader.nextNode()) && size<240) {
      const parent=n.parentElement, value=n.textContent.replace(/\s+/g,' ').trim();
      if (!value || !parent || parent.closest('script,style,noscript,template') || !visible(parent)) continue;
      out.push(value); size+=value.length+1;
    }
    return out.join(' ').slice(0,240);
  };
  for (const root of candidates) {
    if (!root || root===document.body || root.closest(excluded) || !visible(root)) continue;
    const bounds=root.getBoundingClientRect();
    if (!bounds.width || !bounds.height || bounds.bottom<=0 || bounds.top>=innerHeight ||
        bounds.right<=0 || bounds.left>=innerWidth) continue;
    // Only immediate list members belong to this group, not nested lists.
    const rows=[...root.querySelectorAll(rowSelector)].filter(e=>
      (e.closest(listSelector)===root && !root.contains(e.parentElement.closest(rowSelector))) || e.parentElement===root);
    if (rows.length<2) continue;
    if (groups.length>=4) { omittedGroups++; continue; }
    let rendered=0, inViewport=0, below=0, omittedPreview=0;
    const preview=[];
    for (const [index,row] of rows.slice(0,200).entries()) {
      if (row.closest(excluded) || !visible(row)) continue;
      const r=row.getBoundingClientRect();
      if (!r.width || !r.height) continue;
      rendered++;
      if (r.right<=0 || r.left>=innerWidth) continue;
      if (r.bottom>0 && r.top<innerHeight) inViewport++;
      else if (r.top>=innerHeight) {
        below++;
        if (r.top>=innerHeight*2) continue;
        if (preview.length>=6 || previewBudget<=0) { omittedPreview++; continue; }
        const value=rowText(row).slice(0,previewBudget);
        if (value) { preview.push({position:index+1,text:value}); previewBudget-=value.length; }
      }
    }
    if (rendered<2) continue;
    const heading=root.previousElementSibling;
    const label=(root.getAttribute('aria-label') ||
      (root.tagName==='TABLE' && root.caption && visible(root.caption) ? rowText(root.caption) : '') ||
      (heading?.matches('h1,h2,h3,h4,h5,h6,[role="heading"],[role="status"]') ? rowText(heading) : '') || '')
      .replace(/\s+/g,' ').trim().slice(0,240);
    const reported=root.getAttribute('aria-rowcount') || rows[0]?.getAttribute('aria-setsize');
    groups.push({label,rendered_rows:rendered,visible_rows:inViewport,rows_below_viewport:below,
      uninspected_rows:Math.max(0,rows.length-200),
      reported_total:/^\d+$/.test(reported||'') ? Number(reported) : null,
      preview,omitted_preview_rows:omittedPreview});
  }
  const list_context={groups,omitted_groups:omittedGroups};
  const page_key=cache.pageKey(), guards={};
  for (const a of actions) if (!(a.node in guards)) guards[a.node]=cache.guard(cache.nodes.get(a.node));
  // Compare meaning and identity. Geometry is always resolved and hit-tested just before input.
  const semantics=actions.map(({rect,...action})=>action);
  const marker=[performance.timeOrigin,location.href,scrollX,scrollY,innerWidth,innerHeight,
    document.title,text,semantics,list_context,cache.fieldState(document)];
  const omitted_actions=Math.max(0,actions.length-250);
  actions.splice(250);
  actions.forEach((a,i)=>a.id='e'+(i+1));
  if (scrollY+innerHeight<height-2) actions.push({id:'scroll_down',kind:'scroll',label:'Scroll down',delta:560});
  if (scrollY>0) actions.push({id:'scroll_up',kind:'scroll',label:'Scroll up',delta:-560});
  actions.push({id:'wait',kind:'wait',label:'Wait for the page to update'});
  return {url:location.href,title:document.title,w:innerWidth,h:innerHeight,text,
    scroll:{y:scrollY,height},actions,marker,page_key,guards,omitted_actions,list_context};
})()
