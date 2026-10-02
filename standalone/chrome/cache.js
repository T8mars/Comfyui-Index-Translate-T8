let opened;
function database() {
  return (opened ||= new Promise((resolve, reject) => {
    const request = indexedDB.open("index-translate-cache", 1);
    request.onupgradeneeded = () => {
      const store = request.result.createObjectStore("translations", {
        keyPath: "key",
      });
      store.createIndex("time", "time");
    };
    request.onsuccess = () => {
      request.result.onversionchange = () => {
        request.result.close();
        opened = undefined;
      };
      resolve(request.result);
    };
    request.onerror = () => reject(request.error);
  }).catch((error) => {
    opened = undefined;
    throw error;
  }));
}
export async function get(key) {
  const db = await database();
  return new Promise((resolve, reject) => {
    const request = db
      .transaction("translations")
      .objectStore("translations")
      .get(key);
    request.onsuccess = () => resolve(request.result?.value);
    request.onerror = () => reject(request.error);
  });
}
export async function put(key, value) {
  const db = await database();
  return new Promise((resolve, reject) => {
    const tx = db.transaction("translations", "readwrite");
    const store = tx.objectStore("translations");
    store.put({ key, value, time: Date.now() });
    const request = store.count();
    request.onsuccess = () => {
      if (request.result > 2000) {
        let remaining = request.result - 1800;
        store.index("time").openCursor().onsuccess = (event) => {
          const cursor = event.target.result;
          if (cursor && remaining-- > 0) {
            cursor.delete();
            cursor.continue();
          }
        };
      }
    };
    tx.oncomplete = resolve;
    tx.onerror = () => reject(tx.error);
    tx.onabort = () => reject(tx.error || new Error("缓存事务已中止"));
  });
}
export async function clear() {
  const db = await database();
  return new Promise((resolve, reject) => {
    const tx = db.transaction("translations", "readwrite");
    tx.objectStore("translations").clear();
    tx.oncomplete = resolve;
    tx.onerror = () => reject(tx.error);
    tx.onabort = () => reject(tx.error || new Error("缓存事务已中止"));
  });
}
