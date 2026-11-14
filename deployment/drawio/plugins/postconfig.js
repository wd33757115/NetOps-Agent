window.VSD_CONVERT_URL = null;
window.ICONSEARCH_PATH = null;
EditorUi.enableLogging = false; //Disable logging
window.EMF_CONVERT_URL = null;
App.prototype.isDriveDomain = function() { return true; }
// NetOps：允许加载自定义插件（华为设备面板 hw），并注册到插件白名单。
// （在 app.min.js 之后、App init 之前执行；settings 里的插件路径无需 registry，
//  p= 短名路径必须在这里注册。）
window.ALLOW_CUSTOM_PLUGINS = true;
if (typeof App !== 'undefined' && App.pluginRegistry) {
  App.pluginRegistry['hw'] = 'plugins/hw.js';
}
