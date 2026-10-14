import test from 'node:test';
import assert from 'node:assert/strict';
import {effectiveUIPreferences,shortcutMatches} from '../nailong/gui/static/preferences.js';

test('explicit native preferences win while unsaved appearance preserves browser choice',()=>{
  const storage={getItem:key=>key==='ignovate-theme'?'dark':null};
  assert.equal(effectiveUIPreferences({preferences:{theme:'system',font_size:14},saved_keys:[]},storage).theme,'dark');
  assert.equal(effectiveUIPreferences({},storage).font_size,15);
  const saved=effectiveUIPreferences({preferences:{theme:'light',font_size:18,tool_details:'detailed'},saved_keys:['theme','font_size','tool_details']},storage);
  assert.equal(saved.theme,'light');assert.equal(saved.font_size,18);assert.equal(saved.tool_details,'detailed');
});
test('shortcut matching handles platform modifiers, case, exact shift/alt and IME',()=>{
  const event={key:'N',metaKey:true,ctrlKey:false,shiftKey:true,altKey:false,isComposing:false};
  assert.equal(shortcutMatches(event,'Mod+Shift+N'),true);
  assert.equal(shortcutMatches(event,'Mod+N'),false);
  assert.equal(shortcutMatches({...event,ctrlKey:true,metaKey:false},'Mod+Shift+N'),true);
  assert.equal(shortcutMatches({...event,isComposing:true},'Mod+Shift+N'),false);
  assert.equal(shortcutMatches({...event,altKey:true},'Mod+Shift+N'),false);
  assert.equal(shortcutMatches({...event,metaKey:false},'Mod+Shift+N'),false);
});
