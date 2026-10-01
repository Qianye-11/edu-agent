/* 本地补丁（第 ㊼ 条）
   上游 index.html 在 </body> 前引用了 /static/_qisi-splash.js，但本次更新时该文件未随行
   ⇒ 页面控制台报 404。

   ★ 为什么这里是一个「空实现」而不是上游原文：
     上游那份自己也会创建 .qisi-splash（入场动画），且它比本地追加块**更早执行**；
     而它的幂等检查是「已存在就退出」，所以一旦存在，**它就会抢在本地动画之前生效**，
     结果是文案退回上游的旧句「启学，让思考发生」，并绕过本地调过的夜间配色。
     本地真正的入场动画由 _qisi-apple-ui.js 里的 mountSplash() 提供
     （文案「启学，让思考自然发生」+ 日/夜对比度都调过）。
     所以这里保留文件只为消除 404，行为上一律让位给本地实现。
*/
(function () {
  'use strict';
  /* intentionally empty — entrance animation is owned by _qisi-apple-ui.js */
})();
