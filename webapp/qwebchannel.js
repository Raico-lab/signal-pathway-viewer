// ブラウザ版の qwebchannel.js（Qt の同名のファイルの代わり）。
// 地図のページは表示枠ごとの iframe で開く。親のページ（Python 側）が iframe の要素に付けた pvChannel から
// bridge（Python のメソッドを呼ぶ関数）を受け取って、network.js に渡す。
"use strict";
window.qt = window.qt || { webChannelTransport: {} };

window.QWebChannel = function QWebChannel(_transport, callback) {
  const tryConnect = (attempt) => {
    const host = window.frameElement && window.frameElement.pvChannel;
    if (host) {
      callback(host);
    } else if (attempt < 200) {
      setTimeout(() => tryConnect(attempt + 1), 25);
    } else {
      console.error("Python 側とつながりませんでした");
    }
  };
  tryConnect(0);
};
