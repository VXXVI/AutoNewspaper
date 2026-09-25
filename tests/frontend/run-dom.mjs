/* 极简 DOM 测试驱动：执行 app.dom.mjs 里的用例，打印结果并强制退出。
 *
 * 目的：绕开 node:test 的进程隔离 + jsdom 句柄导致的挂起（本机 Node 22）。
 * 失败返回非零退出码，CI 可据此判定。
 */
import { domTests } from "./app.dom.mjs";

let failed = 0;
const started = Date.now();

for (const testCase of domTests) {
  try {
    await testCase.fn();
    console.log(`ok - ${testCase.name}`);
  } catch (error) {
    failed += 1;
    console.error(`not ok - ${testCase.name}`);
    console.error(error && error.stack ? error.stack : error);
  }
}

const total = domTests.length;
console.log(`\n${total - failed}/${total} DOM tests passed in ${Date.now() - started}ms`);
process.exit(failed ? 1 : 0);
