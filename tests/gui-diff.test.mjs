import {test} from 'node:test';
import assert from 'node:assert/strict';
import {parseDiff} from '../nailong/gui/static/diff.js';

test('truncated patch suppresses unknown line offsets until another hunk',()=>{
 const rows=parseDiff('@@ -1,5000 +1,5000 @@\n-old\n[中间内容已截断]\n+new\n@@ -5100 +5100 @@\n-last\n+last-new');
 assert.equal(rows[1].old,1);
 assert.equal(rows[3].next,null);
 assert.equal(rows[5].old,5100);
 assert.equal(rows[6].next,5100);
});
