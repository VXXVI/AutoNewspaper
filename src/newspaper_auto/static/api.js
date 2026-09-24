/* 统一的 JSON 请求层：超时、错误解析和响应格式校验只在一处处理。 */
(function (global) {
  "use strict";

  function ApiError(message, status, data) {
    this.name = "ApiError";
    this.message = message;
    this.status = status || 0;
    this.data = data || null;
    if (Error.captureStackTrace) Error.captureStackTrace(this, ApiError);
  }
  ApiError.prototype = Object.create(Error.prototype);
  ApiError.prototype.constructor = ApiError;

  function request(path, options) {
    options = options || {};
    var timeout = typeof options.timeout === "number" ? options.timeout : 15000;
    var controller = typeof AbortController !== "undefined" ? new AbortController() : null;
    var timer = controller && timeout > 0
      ? window.setTimeout(function () { controller.abort(); }, timeout)
      : null;
    var headers = { "Accept": "application/json" };
    Object.keys(options.headers || {}).forEach(function (name) {
      headers[name] = options.headers[name];
    });
    var init = {
      method: options.method || "GET",
      cache: "no-store",
      headers: headers
    };
    if (controller) init.signal = controller.signal;
    if (options.body !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(options.body);
    }
    return fetch(path, init).then(function (response) {
      return response.json().catch(function () { return null; }).then(function (data) {
        if (!response.ok) {
          var message = data && data.error ? data.error : "请求失败（HTTP " + response.status + "）";
          throw new ApiError(message, response.status, data);
        }
        if (data === null) throw new ApiError("服务器返回了无法识别的数据", response.status, null);
        return data;
      });
    }).catch(function (error) {
      if (error && error.name === "AbortError") throw new ApiError("请求超时，请稍后重试", 0, null);
      if (error instanceof ApiError) throw error;
      if (error instanceof TypeError) throw new ApiError("无法连接本地服务，请确认看板仍在运行", 0, null);
      throw error;
    }).finally(function () {
      if (timer) window.clearTimeout(timer);
    });
  }

  global.NewspaperApi = {
    get: function (path, options) { return request(path, options); },
    post: function (path, body, options) {
      options = Object.assign({}, options || {});
      options.method = "POST";
      options.body = body;
      return request(path, options);
    },
    ApiError: ApiError
  };
})(window);
