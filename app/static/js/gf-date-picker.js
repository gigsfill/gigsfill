/**
 * GfDatePicker — shared dark date picker
 * =======================================
 * Lifted from venue.create-gigs.js (2026-05 version). Provides a text input
 * + custom popup so date pickers look consistent (dark card + purple accents)
 * across browsers, since native input[type=date] popups couldn't be reliably
 * themed dark in Safari + some Chrome configs.
 *
 * Usage:
 *   <input type="text" id="foo" readonly class="gf-date-input" placeholder="mm/dd/yyyy">
 *   new GfDatePicker(document.getElementById('foo'));
 *
 * Accepts both mm/dd/yyyy (its own display format) and yyyy-mm-dd (legacy)
 * so pre-populated ISO strings restore correctly. Emits a 'change' event on
 * pick so downstream form validation still fires.
 *
 * Self-injects its own CSS once on module load — pages loading this script
 * get the popup styling for free.
 */
(function () {
  'use strict';

  // ── CSS (injected once) ─────────────────────────────────────────────
  if (!document.getElementById('gf-date-picker-css')) {
    const s = document.createElement('style');
    s.id = 'gf-date-picker-css';
    s.textContent = `
      .gf-date-input { cursor: pointer; }
      .gf-date-input:disabled { cursor: not-allowed; }
      .gf-date-popup {
        position: absolute; background: #1a1a2e;
        border: 1px solid rgba(124, 107, 255, 0.45); border-radius: 8px;
        padding: 10px; box-shadow: 0 12px 32px rgba(0, 0, 0, 0.6);
        z-index: 100000; user-select: none; min-width: 248px;
        font-family: var(--font-primary, -apple-system, BlinkMacSystemFont, sans-serif);
      }
      .gf-date-header {
        display: flex; align-items: center; justify-content: space-between;
        margin-bottom: 8px; padding: 0 2px;
      }
      .gf-date-title {
        background: linear-gradient(135deg, #a855f7, #06b6d4);
        -webkit-background-clip: text; background-clip: text;
        -webkit-text-fill-color: transparent;
        font-weight: 700; font-size: 0.88rem; letter-spacing: 0.02em;
      }
      .gf-date-nav {
        background: rgba(124, 107, 255, 0.10);
        border: 1px solid rgba(124, 107, 255, 0.25);
        color: #c4b5fd; cursor: pointer; width: 26px; height: 26px;
        border-radius: 5px; font-size: 0.85rem; line-height: 1;
        display: inline-flex; align-items: center; justify-content: center;
        transition: background .12s, border-color .12s;
      }
      .gf-date-nav:hover {
        background: rgba(124, 107, 255, 0.22);
        border-color: rgba(124, 107, 255, 0.55);
      }
      .gf-date-grid {
        display: grid; grid-template-columns: repeat(7, 32px); gap: 2px;
      }
      .gf-date-dow {
        text-align: center; font-size: 0.62rem; color: rgba(255,255,255,0.4);
        padding: 4px 0; font-weight: 700; letter-spacing: 0.05em;
      }
      .gf-date-cell {
        width: 32px; height: 28px; background: transparent;
        border: 1px solid transparent; color: #d1d5db; font-size: 0.78rem;
        font-weight: 500; cursor: pointer; border-radius: 4px; padding: 0;
        line-height: 1; transition: background .10s, border-color .10s;
      }
      .gf-date-cell:hover {
        background: rgba(124, 107, 255, 0.18);
        border-color: rgba(124, 107, 255, 0.40);
      }
      .gf-date-cell.today {
        border-color: rgba(34, 197, 94, 0.55); color: #86efac; font-weight: 700;
      }
      .gf-date-cell.selected {
        background: linear-gradient(135deg, #a855f7, #7c3aed);
        color: #fff; border-color: rgba(168,85,247,0.8); font-weight: 700;
      }
      .gf-date-cell.selected:hover {
        background: linear-gradient(135deg, #c084fc, #a855f7);
      }
      .gf-date-cell.empty { cursor: default; pointer-events: none; }
      .gf-date-footer {
        display: flex; justify-content: space-between;
        margin-top: 8px; padding-top: 8px;
        border-top: 1px solid rgba(255,255,255,0.08);
      }
      .gf-date-footer button {
        background: transparent; border: 0; color: #a78bfa;
        font-size: 0.72rem; font-weight: 600; cursor: pointer;
        padding: 2px 6px; border-radius: 4px;
      }
      .gf-date-footer button:hover {
        background: rgba(124, 107, 255, 0.15); color: #c4b5fd;
      }
    `;
    document.head.appendChild(s);
  }

  class GfDatePicker {
    constructor(input) {
      this.input = input;
      this.popup = null;
      this.viewDate = new Date();
      this.outsideHandler = null;
      this.repositionHandler = null;
      input.addEventListener('click', () => { if (!input.disabled) this.open(); });
      input.addEventListener('keydown', (e) => {
        if (input.disabled) return;
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); this.open(); }
        if (e.key === 'Escape') this.close();
      });
    }
    _parseValue() {
      const v = (this.input.value || '').trim();
      let m = v.match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})$/);
      if (m) {
        const d = new Date(+m[3], +m[1] - 1, +m[2]); d.setHours(0,0,0,0); return d;
      }
      m = v.match(/^(\d{4})-(\d{2})-(\d{2})$/);
      if (m) {
        const d = new Date(+m[1], +m[2] - 1, +m[3]); d.setHours(0,0,0,0); return d;
      }
      return null;
    }
    _toDisp(d) {
      const mo = String(d.getMonth() + 1).padStart(2, '0');
      const da = String(d.getDate()).padStart(2, '0');
      return `${mo}/${da}/${d.getFullYear()}`;
    }
    // Serialize the current input value to yyyy-mm-dd (for backend). Returns
    // '' when the input is empty or unparseable.
    getISO() {
      const d = this._parseValue();
      if (!d) return '';
      const mo = String(d.getMonth() + 1).padStart(2, '0');
      const da = String(d.getDate()).padStart(2, '0');
      return `${d.getFullYear()}-${mo}-${da}`;
    }
    // Populate from a yyyy-mm-dd string (silent — no change event fires).
    setISO(iso) {
      if (!iso) { this.input.value = ''; return; }
      const m = String(iso).match(/^(\d{4})-(\d{2})-(\d{2})$/);
      if (!m) return;
      const d = new Date(+m[1], +m[2] - 1, +m[3]);
      d.setHours(0,0,0,0);
      this.input.value = this._toDisp(d);
    }
    open() {
      if (this.popup) return;
      const sel = this._parseValue();
      this.viewDate = sel ? new Date(sel) : new Date();
      this.viewDate.setDate(1);
      this.popup = document.createElement('div');
      this.popup.className = 'gf-date-popup';
      document.body.appendChild(this.popup);
      this._render();
      this._position();
      setTimeout(() => {
        this.outsideHandler = (e) => {
          if (!this.popup) return;
          if (!this.popup.contains(e.target) && e.target !== this.input) this.close();
        };
        document.addEventListener('mousedown', this.outsideHandler);
      }, 0);
      this.repositionHandler = () => this._position();
      window.addEventListener('resize', this.repositionHandler);
      window.addEventListener('scroll', this.repositionHandler, true);
    }
    close() {
      if (!this.popup) return;
      this.popup.remove();
      this.popup = null;
      if (this.outsideHandler) {
        document.removeEventListener('mousedown', this.outsideHandler);
        this.outsideHandler = null;
      }
      if (this.repositionHandler) {
        window.removeEventListener('resize', this.repositionHandler);
        window.removeEventListener('scroll', this.repositionHandler, true);
        this.repositionHandler = null;
      }
    }
    _position() {
      if (!this.popup) return;
      const r = this.input.getBoundingClientRect();
      this.popup.style.left = (r.left + window.scrollX) + 'px';
      this.popup.style.top = (r.bottom + window.scrollY + 4) + 'px';
    }
    _render() {
      const selDate = this._parseValue();
      const year = this.viewDate.getFullYear();
      const month = this.viewDate.getMonth();
      const monthNames = ['January','February','March','April','May','June',
                          'July','August','September','October','November','December'];
      const firstDay = new Date(year, month, 1);
      const startDow = firstDay.getDay();
      const daysInMonth = new Date(year, month + 1, 0).getDate();
      const today = new Date(); today.setHours(0,0,0,0);
      const cells = [];
      for (let i = 0; i < startDow; i++) cells.push(`<span class="gf-date-cell empty"></span>`);
      for (let d = 1; d <= daysInMonth; d++) {
        const c = new Date(year, month, d); c.setHours(0,0,0,0);
        const isToday = c.getTime() === today.getTime();
        const isSel = selDate && c.getTime() === selDate.getTime();
        const cls = ['gf-date-cell'];
        if (isToday) cls.push('today');
        if (isSel) cls.push('selected');
        cells.push(`<button type="button" class="${cls.join(' ')}" data-day="${d}">${d}</button>`);
      }
      this.popup.innerHTML = `
        <div class="gf-date-header">
          <button type="button" class="gf-date-nav" data-dir="-1" aria-label="Previous month">&#9664;</button>
          <span class="gf-date-title">${monthNames[month]} ${year}</span>
          <button type="button" class="gf-date-nav" data-dir="1" aria-label="Next month">&#9654;</button>
        </div>
        <div class="gf-date-grid">
          <span class="gf-date-dow">S</span><span class="gf-date-dow">M</span>
          <span class="gf-date-dow">T</span><span class="gf-date-dow">W</span>
          <span class="gf-date-dow">T</span><span class="gf-date-dow">F</span>
          <span class="gf-date-dow">S</span>
          ${cells.join('')}
        </div>
        <div class="gf-date-footer">
          <button type="button" data-action="today">Today</button>
          <button type="button" data-action="clear">Clear</button>
        </div>
      `;
      this.popup.querySelectorAll('.gf-date-nav').forEach((btn) => {
        btn.addEventListener('click', (e) => {
          e.stopPropagation();
          const dir = parseInt(btn.dataset.dir, 10);
          this.viewDate = new Date(this.viewDate.getFullYear(), this.viewDate.getMonth() + dir, 1);
          this._render();
        });
      });
      this.popup.querySelectorAll('.gf-date-cell[data-day]').forEach((cell) => {
        cell.addEventListener('click', (e) => {
          e.stopPropagation();
          const d = parseInt(cell.dataset.day, 10);
          this.input.value = this._toDisp(new Date(year, month, d));
          this.input.dispatchEvent(new Event('change', { bubbles: true }));
          this.close();
        });
      });
      this.popup.querySelectorAll('[data-action]').forEach((btn) => {
        btn.addEventListener('click', (e) => {
          e.stopPropagation();
          const act = btn.dataset.action;
          if (act === 'today') {
            const t = new Date(); t.setHours(0,0,0,0);
            this.input.value = this._toDisp(t);
            this.input.dispatchEvent(new Event('change', { bubbles: true }));
            this.close();
          } else if (act === 'clear') {
            this.input.value = '';
            this.input.dispatchEvent(new Event('change', { bubbles: true }));
            this.close();
          }
        });
      });
    }
  }

  window.GfDatePicker = GfDatePicker;
})();
