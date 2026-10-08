(() => {
  'use strict';
  const data = JSON.parse(document.getElementById('lens-data').textContent);
  const $ = id => document.getElementById(id);
  const kinds = {Capability:'能力',Component:'组件',Scenario:'场景',Ownership:'职责',Limitation:'限制',Uncertainty:'未知',Mechanism:'机制步骤',Audience:'受众角色',Rule:'表达规则'};
  const ruleKinds={focus:'关注重点',language:'语言规则',analogy:'类比规则',tone:'语气规则',depth:'细节深度',structure:'组织方式'};
  const meanings=data.meaningObjects||[];
  const sources = {doc:'文档证据',code:'代码证据',trace:'分析观察'};
  const nodes = new Map(data.nodes.map(n => [n.id,n]));
  const evidence = new Map(data.evidence.map(e => [e.id,e]));
  const spanForEvidence = new Map();
  data.nodes.filter(n=>n.sourceContext?.kind==='markdown-span').forEach(n=>(n.evidence||[]).forEach(id=>{if(!spanForEvidence.has(id))spanForEvidence.set(id,n);}));
  const blocks={frontmatter:'触发描述',heading_open:'章节标题',paragraph_open:'指令段落',table_open:'规则表格',bullet_list_open:'规则列表',ordered_list_open:'步骤列表',fence:'示例代码',code_block:'示例代码'};
  const relationNames={contains:'包含',declares:'声明'};
  function sourceName(n){
    const context=n.sourceContext;
    if(context?.kind!=='markdown-span')return n.label;
    const ref=evidence.get(n.evidence?.[0]);
    const heading=context.headingPath?.at(-1);
    if(context.blockType==='frontmatter')return '名称与触发描述';
    if(context.blockType==='heading_open')return heading||n.label;
    const first=String(ref?.quote||'').split('\n').find(line=>line.trim())||n.label;
    const excerpt=first.replace(/^\s*(?:[-*+]\s+|\d+[.)]\s+)/,'').replace(/\*\*/g,'');
    // The heading and the actual opening words identify distinct spans in the
    // same file. These are source labels, not newly inferred capabilities.
    if(context.blockType==='table_open')return heading?`${heading} · 规则表格`:excerpt;
    return heading?`${excerpt} · ${heading}`:excerpt;
  }
  function sourceLocation(n){const ref=evidence.get(n.evidence?.[0]);return ref?`${ref.source.path}:${ref.source.lines}`:n.id;}
  function objectName(o){if(o.semantic)return o.label;const n=o.evidenceNodeIds.length===1?nodes.get(o.evidenceNodeIds[0]):null;return data.projectionMode==='rule-based-v0.1'&&n?.sourceContext?.kind==='markdown-span'?sourceName(n):o.label;}
  function isDocumentStructure(edges){return edges.length>0&&edges.every(e=>['contains','declares'].includes(e.type)&&[e.from,e.to].every(id=>{const n=nodes.get(id);return (n.evidence||[]).some(ref=>evidence.get(ref)?.sourceType==='doc');}));}
  const adjacency = new Map();
  data.edges.forEach(edge => [edge.from,edge.to].forEach(id => {if(!adjacency.has(id))adjacency.set(id,[]);adjacency.get(id).push(edge);}));
  const firstInstruction=data.instructionAnalysis?.stages.flatMap(stage=>stage.evidenceNodeIds).find(id=>['paragraph_open','table_open','bullet_list_open','ordered_list_open'].includes(nodes.get(id)?.sourceContext?.blockType));
  const initial=meanings[0]||data.objects.find(o=>o.evidenceNodeIds.includes(firstInstruction))||data.objects[0];
  const state = {mode:meanings.length?'meaning':'source',kind:'all',query:'',page:Math.floor((meanings.length?meanings:data.objects).indexOf(initial)/12),selected:initial?.id,view:'citations',evidencePage:0,edgePage:0};
  function element(tag,text,className) {const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(className)e.className=className;return e;}
  function button(text,action) {const b=element('button',text);b.type='button';b.addEventListener('click',action);return b;}
  function short(text,max) {const s=Array.from(String(text));return s.length>max?s.slice(0,max-1).join('')+'…':s.join('');}
  function fitText(text,width,size=16){const chars=Array.from(String(text)),cost=c=>size*(/[\u2e80-\u9fff\uff00-\uffef]/u.test(c)?1:.62);if(chars.reduce((total,c)=>total+cost(c),0)<=width)return String(text);let used=0,result='';for(const c of chars){if(used+cost(c)>width-size)break;result+=c;used+=cost(c);}return result+'…';}
  function collection(){return state.mode==='meaning'?meanings:data.objects;}
  function object() {return collection().find(o=>o.id===state.selected);}
  function matches() {return collection().filter(o=>(state.kind==='all'||o.type===state.kind)&&(!state.query||[o.label,o.summary,...o.evidenceIds.map(id=>{const e=evidence.get(id);return `${e.source.path} ${e.quote}`;})].join(' ').toLocaleLowerCase().includes(state.query)));}
  function openMeaning(id){state.mode='meaning';state.kind='all';state.query='';$('search').value='';state.selected=id;state.page=Math.max(0,Math.floor(meanings.findIndex(o=>o.id===id)/12));renderList();renderDetail();}
  function renderList() {
    const items=matches(), pages=Math.max(1,Math.ceil(items.length/12));state.page=Math.min(state.page,pages-1);
    if(!items.some(o=>o.id===state.selected)){state.selected=items[0]?.id;state.evidencePage=0;state.edgePage=0;}
    $('reading-mode').replaceChildren(...(meanings.length?['meaning','source'].map(mode=>{const b=button(mode==='meaning'?'理解能力':'核对源码',()=>{state.mode=mode;state.kind='all';state.query='';state.page=0;state.evidencePage=0;state.edgePage=0;$('search').value='';renderList();renderDetail();});b.setAttribute('aria-pressed',String(mode===state.mode));return b;}):[]));
    $('categories').replaceChildren(...['all',...Object.keys(kinds)].filter(k=>k==='all'||collection().some(o=>o.type===k)).map(k=>{const count=k==='all'?collection().length:collection().filter(o=>o.type===k).length;const b=button(`${kinds[k]||'全部'} ${count}`,()=>{state.kind=k;state.page=0;renderList();renderDetail();});b.setAttribute('aria-pressed',String(k===state.kind));return b;}));
    $('list-status').textContent=items.length?`${items.length} 项 · 第 ${state.page+1}/${pages} 页`:'没有匹配项；换一个关键词或解释类型。';
    $('object-list').replaceChildren(...items.slice(state.page*12,state.page*12+12).map(o=>{const b=button('',()=>{state.selected=o.id;state.evidencePage=0;state.edgePage=0;renderList();renderDetail();});b.className='object-button';b.setAttribute('aria-current',String(o.id===state.selected));b.title=o.label;b.append(element('span',kinds[o.type]||o.type),element('div',objectName(o)));return b;}));
    const prev=button('上一页',()=>{state.page--;renderList();}),next=button('下一页',()=>{state.page++;renderList();});prev.disabled=state.page===0;next.disabled=state.page>=pages-1;$('pages').replaceChildren(prev,next);
  }
  const NS='http://www.w3.org/2000/svg';
  function svgElement(tag,attrs,text) {const e=document.createElementNS(NS,tag);Object.entries(attrs||{}).forEach(([k,v])=>e.setAttribute(k,v));if(text!==undefined)e.textContent=text;return e;}
  function figure(title,desc,height,width=960) {
    const s=svgElement('svg',{viewBox:`0 0 ${width} ${height}`,role:'img',style:`min-width:${width}px`, 'aria-labelledby':'lens-figure-title lens-figure-desc'});
    s.append(svgElement('title',{id:'lens-figure-title'},title),svgElement('desc',{id:'lens-figure-desc'},desc));
    const defs=svgElement('defs'),marker=svgElement('marker',{id:'lens-arrow',markerWidth:8,markerHeight:6,refX:7,refY:3,orient:'auto'});
    marker.append(svgElement('polygon',{points:'0 0,8 3,0 6',fill:'var(--muted)'}));defs.append(marker);s.append(defs);return s;
  }
  function box(s,x,y,w,h,name,tag,sub,focal=false) {
    const g=svgElement('g');g.append(svgElement('title',{},String(name)),svgElement('rect',{x,y,width:w,height:h,rx:6,class:focal?'diagram-node diagram-focal':'diagram-node'}));
    g.append(svgElement('text',{x:x+16,y:y+24,class:'svg-label'},tag));
    g.append(svgElement('text',{x:x+16,y:y+52},fitText(name,w-32)));
    if(h>=96)g.append(svgElement('text',{x:x+16,y:y+80,class:'svg-tech'},fitText(sub,w-32,12)));s.append(g);
  }
  function label(s,x,y,text) {const width=Math.min(220,Math.max(48,Array.from(text).length*13+16));s.append(svgElement('rect',{x:x-width/2,y:y-18,width,height:24,rx:2,class:'diagram-label-mask'}),svgElement('text',{x,y,'text-anchor':'middle',class:'svg-label'},text));}
  function fanFigure(root,children,title,description,connector){
    const n=children.length;
    const width=n===1?640:960, rootX=(width-320)/2;
    const layouts={1:[[160,320]],2:[[84,352],[524,352]],3:[[48,264],[348,264],[648,264]],4:[[48,200],[268,200],[492,200],[712,200]]};
    const s=figure(title,description,408,width);
    const positions=children.map((child,i)=>({...child,x:layouts[n][i][0],w:layouts[n][i][1]}));
    positions.forEach((p,i)=>{const start=Math.round((rootX+320*(i+1)/(n+1))/4)*4,end=p.x+p.w/2,level=Math.round((192-(Math.abs(i-(n-1)/2)*32))/4)*4;
      let d;if(Math.abs(end-start)<1)d=`M ${start} 136 V 264`;else {const dir=end>start?1:-1;d=`M ${start} 136 V ${level-8} Q ${start} ${level} ${start+dir*8} ${level} H ${end-dir*8} Q ${end} ${level} ${end} ${level+8} V 264`;}
      s.append(svgElement('path',{d,class:'diagram-connector','marker-end':'url(#lens-arrow)'}));
      if(Math.abs(end-start)>80)label(s,(start+end)/2,level-16,connector);else label(s,start+48,208,connector);
    });
    box(s,rootX,40,320,96,root.name,root.tag,root.sub,true);
    positions.forEach(p=>box(s,p.x,264,p.w,104,p.name,p.tag,p.sub));
    return s;
  }
  function citationFigure(o){
    const children=o.evidenceIds.slice(state.evidencePage*4,state.evidencePage*4+4).map(id=>{const e=evidence.get(id),n=spanForEvidence.get(id);return {name:n?sourceName(n):e.quote||e.source.path,tag:n?(blocks[n.sourceContext.blockType]||'原文片段'):(sources[e.sourceType]||e.sourceType),sub:`${e.source.path}:${e.source.lines}`};});
    return fanFigure({name:objectName(o),tag:'当前解释',sub:o.id},children,'解释引用的原文','箭头表示引用，原文节点以内容和行号区分，不表示执行。','引用');
  }
  function related(o) {return [...new Map(o.evidenceNodeIds.flatMap(id=>adjacency.get(id)||[]).map(e=>[e.id,e])).values()].sort((a,b)=>Number(nodes.get(b.to)?.sourceContext?.kind==='markdown-span')-Number(nodes.get(a.to)?.sourceContext?.kind==='markdown-span'));}
  function relationFigure(edges) {
    const documentOnly=isDocumentStructure(edges);
    if(documentOnly&&edges.every(e=>e.from===edges[0].from&&e.type==='contains')){
      const root=nodes.get(edges[0].from);
      const children=edges.map(e=>{const n=nodes.get(e.to);return {name:sourceName(n),tag:blocks[n.sourceContext?.blockType]||'文档条目',sub:sourceLocation(n)};});
      return fanFigure({name:sourceName(root),tag:'所属文档',sub:sourceLocation(root)},children,'文档与其中的原文片段','同一个文件只出现一次；包含关系不是规则之间的调用或依赖。','包含');
    }
    const s=figure(documentOnly?'文档结构切片':'源码关系切片','每一行是一条原始 Evidence Graph 关系；行与行不代表执行顺序。',edges.length*152+48);
    edges.forEach((e,i)=>{const y=24+i*152;s.append(svgElement('line',{x1:344,y1:y+52,x2:616,y2:y+52,class:'diagram-connector','marker-end':'url(#lens-arrow)'}));label(s,480,y+36,short(relationNames[e.type]||e.type,14));});
    edges.forEach((e,i)=>{const y=24+i*152;const from=nodes.get(e.from),to=nodes.get(e.to);box(s,40,y,304,104,sourceName(from),blocks[from.sourceContext?.blockType]||from.type,sourceLocation(from));box(s,616,y,304,104,sourceName(to),blocks[to.sourceContext?.blockType]||to.type,sourceLocation(to));});return s;
  }
  function evidenceDetails(refIds,parent) {
    refIds.forEach(id=>{const e=evidence.get(id),d=element('details');
      const summary=element('summary');summary.append(element('span',sources[e.sourceType]||e.sourceType,'source-kind'),element('span',`${e.source.path}:${e.source.lines}`,'evidence-path'));d.append(summary);
      d.append(element('p',`源码版本 ${e.source.revision||data.revision} · ${e.id}`,'source-location'),element('div',e.quote||'此 Evidence 没有引用文本。','quote'));parent.append(d);
    });
  }
  function meaningLocation(o){const selected=o.selectedEvidence?.[0],ref=selected||evidence.get(o.evidenceIds[0]);return ref?`${ref.source.path}:${ref.source.lines}`:o.id;}
  function selectedQuoteDetails(o,target){
    if(!o.selectedEvidence?.length){evidenceDetails(o.evidenceIds,target);return;}
    o.selectedEvidence.forEach(ref=>{const d=element('details');d.append(element('summary',`规则原文 · ${ref.source.path}:${ref.source.lines}`),element('div',ref.quote,'quote'));target.append(d);});
    const full=element('details');full.append(element('summary','完整来源与上下文'));evidenceDetails(o.evidenceIds,full);target.append(full);
  }
  function renderMeaning(target,o){
    target.append(element('div',kinds[o.type],'kind'),element('h2',o.label),element('p',o.summary,'prose'));
    if(o.interpretation)target.append(element('p',`作用解释：${o.interpretation}`,'prose'));
    target.append(element('p','节点以指令的含义命名；文件与行号只用于核对出处。以下是基于文档的语义拆解，未经实际执行验证。','reading-note'));
    const parent=o.type==='Rule'?meanings.find(item=>item.id===o.parentId):o;
    const children=o.type==='Rule'?[o]:(o.ruleIds||[]).map(id=>meanings.find(item=>item.id===id));
    if(children.length){
      const wrapper=element('div',undefined,'diagram-scroll');
      wrapper.append(fanFigure({name:parent.label,tag:'受众角色',sub:'解释要说给谁听'},children.map(rule=>({name:rule.label,tag:ruleKinds[rule.kind]||'表达规则',sub:meaningLocation(rule)})),'受众与适用的表达规则','箭头表示文档规定对这类读者采用这些规则；角色不是执行任务的 Agent。','适用'));
      target.append(wrapper,element('p','连线表示“此受众适用这些表达要求”。这些规则可以来自同一文件的不同行，也可以共同引用同一表格中的某一角色行。','diagram-caption'));
      children.forEach(rule=>{const section=element('section');section.append(element('h3',rule.label),element('p',rule.summary));selectedQuoteDetails(rule,section);target.append(section);});
      if(o.type==='Rule')target.append(button(`查看${parent.label}的全部规则`,()=>openMeaning(parent.id)));
    }else if(o.type==='Audience')target.append(element('p','当前分析只记录了该角色的整体规则，尚未细分子规则；不根据文件共享关系补画语义连线。','reading-note'));
    target.append(element('h3','核对该内容单元的原文'));evidenceDetails(o.evidenceIds,target);
  }
  function renderDetail() {
    const target=$('detail'),o=object();target.replaceChildren();if(!o){target.append(element('p','没有匹配的解释。清除搜索或切换类型后继续查看。','empty'));return;}
    if(o.semantic){renderMeaning(target,o);return;}
    target.append(element('div',`${kinds[o.type]||o.type} · ${o.evidenceIds.length} 段证据`,'kind'),element('h2',objectName(o)),element('p',o.summary,'prose'));
    target.append(element('p',data.projectionMode==='rule-based-v0.1'?'当前为规则解释：用于定位源码，不是模型生成的语义回答。':'当前为已有 Projection 的解释；引用关系经过验证，语义正确性仍需核查。','reading-note'));
    const documentOnly=isDocumentStructure(related(o));
    const tabs=element('div',undefined,'section-tabs');['citations','relations'].forEach(v=>{const b=button(v==='citations'?'原文依据':documentOnly?'文档结构':'源码关系',()=>{state.view=v;renderDetail();});b.setAttribute('aria-pressed',String(state.view===v));tabs.append(b);});target.append(tabs);
    const wrapper=element('div',undefined,'diagram-scroll');
    if(state.view==='citations'){
      const inventoryOnly=o.evidenceNodeIds.every(id=>{const n=nodes.get(id);return n?.sourceContext?.kind!=='markdown-span'&&String(n?.label||'').toLowerCase().endsWith('.md');});
      if(o.evidenceIds.length>1&&!inventoryOnly){wrapper.append(citationFigure(o));target.append(wrapper);}
      else target.append(element('p',inventoryOnly?'这里只确认文档文件存在，不能说明能力机制；直接核对记录即可。':'该条目对应一段原文，直接阅读下面的证据即可。','reading-note'));
      const count=Math.max(1,Math.ceil(o.evidenceIds.length/4));
      if(o.evidenceIds.length>1&&!inventoryOnly)target.append(element('p',`引用关系，不代表执行顺序。图中显示第 ${state.evidencePage+1}/${count} 组；下方可查看全部 ${o.evidenceIds.length} 段原文。`,'diagram-caption'));
      if(count>1&&!inventoryOnly){const pager=element('div',undefined,'pages');const prev=button('上一组证据',()=>{state.evidencePage--;renderDetail();}),next=button('下一组证据',()=>{state.evidencePage++;renderDetail();});prev.disabled=state.evidencePage===0;next.disabled=state.evidencePage===count-1;pager.append(prev,next);target.append(pager);}
      target.append(element('h3','核对源码证据'));evidenceDetails(o.evidenceIds,target);
    }else{
      const edges=related(o),count=Math.max(1,Math.ceil(edges.length/4));state.edgePage=Math.min(state.edgePage,count-1);
      const selected=edges.slice(state.edgePage*4,state.edgePage*4+4);
      if(documentOnly)target.append(element('p','这里只显示文档包含或声明了哪些内容。同一文件里的不同节点是不同原文片段；这些连线不能解释规则如何协作，也不是 Agent 的执行路径。','reading-note'));
      if(edges.length){wrapper.append(relationFigure(selected));target.append(wrapper);}else target.append(element('p','这些引用节点没有已记录的源码关系；不补画推测连线。','empty'));
      target.append(element('p',`${edges.length} 条直接关联 · 第 ${state.edgePage+1}/${count} 组。每一行独立，不构成执行时间线。长标签仅在图上缩略，原始关系记录完整保留。`,'diagram-caption'));
      if(count>1){const pager=element('div',undefined,'pages');const prev=button('上一组关系',()=>{state.edgePage--;renderDetail();}),next=button('下一组关系',()=>{state.edgePage++;renderDetail();});prev.disabled=state.edgePage===0;next.disabled=state.edgePage===count-1;pager.append(prev,next);target.append(pager);}
      selected.forEach(e=>{const d=element('details');d.append(element('summary',`${sourceName(nodes.get(e.from))} → ${relationNames[e.type]||e.type} → ${sourceName(nodes.get(e.to))}`),element('pre',JSON.stringify(e,null,2)));if(e.evidence?.length)evidenceDetails(e.evidence,d);else d.append(element('p','此原始关系没有单独附带 Evidence；不能据此补充语义结论。','subtle'));target.append(d);});
    }
  }
  const mode=meanings.length?'源码索引 + 指令语义解读':data.projectionMode==='rule-based-v0.1'?'规则解释':data.projectionMode;
  $('report-meta').textContent=`${mode} · ${data.status==='partial'?'部分覆盖':data.status} · ${data.objects.length} 个源码条目${meanings.length?` · ${meanings.length} 个内容单元`:''} · 源码 ${data.revision}`;
  $('diagnostic-summary').textContent=`分析诊断与来源记录 · ${data.diagnostics.length} 项诊断`;
  $('diagnostic-content').append(element('pre',JSON.stringify({providers:data.providers,projectionMode:data.projectionMode,provenance:data.provenance,hashes:data.hashes,diagnostics:data.diagnostics},null,2)));
  $('search').addEventListener('input',event=>{state.query=event.target.value.trim().toLocaleLowerCase();state.page=0;renderList();renderDetail();});
  $('theme').addEventListener('click',()=>{const dark=document.documentElement.dataset.theme==='dark'||(!document.documentElement.dataset.theme&&matchMedia('(prefers-color-scheme: dark)').matches);document.documentElement.dataset.theme=dark?'light':'dark';$('theme').textContent=dark?'切换深色':'切换浅色';});
  $('print').addEventListener('click',()=>window.print());renderList();renderDetail();
  document.querySelectorAll('[data-meaning-target]').forEach(link=>link.addEventListener('click',()=>openMeaning(link.dataset.meaningTarget)));
  // The mechanism explanation is server-rendered. JS only adds comparison
  // controls; all illustrative outputs remain available without JS/in print.
  const examples=[...document.querySelectorAll('.illustration')];
  const selector=document.querySelector('.example-selector');
  if(selector&&examples.length>1){
    let selected=0;
    const controls=examples.map((example,index)=>button(example.querySelector('h4').textContent,()=>{selected=index;updateExamples();}));
    function updateExamples(){examples.forEach((example,index)=>{example.hidden=index!==selected;controls[index].setAttribute('aria-pressed',String(index===selected));});}
    selector.hidden=false;selector.replaceChildren(...controls);updateExamples();
  }
})();
