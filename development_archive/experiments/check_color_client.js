const fs=require('fs');
class i{constructor(){this._randTimes=0}static get instance(){return this._instance||(this._instance=new i),this._instance}setSeed(e){if(!Array.isArray(e)||4!==e.length)throw new TypeError("seed must be an array with 4 numbers");this._randTimes=0,this._state0U=0|e[0],this._state0L=0|e[1],this._state1U=0|e[2],this._state1L=0|e[3]}randomint(){var e=this._state0U,t=this._state0L,n=this._state1U,i=this._state1L,o=(i>>>0)+(t>>>0),a=n+e+(o/2>>>31)>>>0,s=o>>>0;this._state0U=n,this._state0L=i;var r=0,c=0;return r=(e^=r=e<<23|(-512&t)>>>9)^n,c=(t^=c=t<<23)^i,r^=e>>>18,c^=t>>>18|(262143&e)<<14,r^=n>>>5,c^=i>>>5|(31&n)<<27,this._state1U=r,this._state1L=c,[a,s]}random(){this._randTimes++;var e=this.randomint();return 2.3283064365386963e-10*e[0]+2220446049250313e-31*(e[1]>>>12)}get randTimes(){return this._randTimes}};

const map=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));
const cards=Object.entries(map.levelData).flatMap(([layer,cs])=>cs.map(c=>({...c,layer:+layer})));
const special=cards.filter(c=>c.metaType===2).sort((a,b)=>a.layer-b.layer||a.rowNum-b.rowNum||a.rolNum-b.rolNum);
const rng=new i();rng.setSeed(map.map_seed);rng.random();
for(let k=0;k<Object.values(map.blockTypeData).reduce((a,b)=>a+b,0)*3;k++)rng.random();
function shuffle(values){for(let k=values.length-1;k>=0;k--){let j=Math.floor(rng.random()*(k+1));[values[k],values[j]]=[values[j],values[k]];}return values;}
function change(){const values=special.map(c=>c.type);if(values.length>1)shuffle(values);special.forEach((c,k)=>c.type=values[k]);}
change();const frames=[{types:Object.fromEntries(special.map(c=>[c.id,c.type])),draws:rng.randTimes}];
for(let step=0;special.length;step++){if(step%2===0)special.splice((step*7)%special.length,1);change();frames.push({types:Object.fromEntries(special.map(c=>[c.id,c.type])),draws:rng.randTimes});}
console.log(JSON.stringify(frames));
