// Sequential job queue: a newer job with the same key replaces a pending one and moves
// to the end, so slider drags send only their latest value and never overtake later work.

export class JobQueue {
  constructor() {
    this.jobs = new Map();
    this.running = false;
    this.idleWaiters = [];
  }

  /** Queue job(); resolves/rejects with its result, or resolves undefined if replaced. */
  submit(key, job) {
    return new Promise((resolve, reject) => {
      const previous = this.jobs.get(key);
      if (previous) {
        this.jobs.delete(key);
        previous.resolve(undefined);
      }
      this.jobs.set(key, { job, resolve, reject });
      this.pump();
    });
  }

  pending(key) {
    return this.jobs.has(key);
  }

  clear() {
    for (const entry of this.jobs.values()) entry.resolve(undefined);
    this.jobs.clear();
  }

  /** Resolves once every queued job has run. */
  idle() {
    if (!this.running && !this.jobs.size) return Promise.resolve();
    return new Promise((resolve) => this.idleWaiters.push(resolve));
  }

  async pump() {
    if (this.running) return;
    this.running = true;
    while (this.jobs.size) {
      const [key, entry] = this.jobs.entries().next().value;
      this.jobs.delete(key);
      try {
        entry.resolve(await entry.job());
      } catch (err) {
        entry.reject(err);
      }
    }
    this.running = false;
    this.idleWaiters.splice(0).forEach((resolve) => resolve());
  }
}
