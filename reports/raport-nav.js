/* Belka nawigacyjna raportu ZKM: chowa się przy przewijaniu w dół, wraca przy przewijaniu w górę.
 * Słucha tylko zdarzenia scroll (passive). Nie przewija strony, nie blokuje gestów i nie zmienia układu
 * strony: belka przesuwa się przez transform. Elementy przyklejone pod belką czytają --rn-off. */
(function () {
  "use strict";
  const nav = document.getElementById("rn");
  if (!nav) return;
  const root = document.documentElement;
  const STEP = 8;              // minimalny ruch (px), który zmienia stan belki
  let lastY = Math.max(0, window.scrollY), hidden = false, ticking = false, height = nav.offsetHeight;

  function setVars() {
    root.style.setProperty("--rn-h", height + "px");
    root.style.setProperty("--rn-off", hidden ? "0px" : height + "px");
  }
  function setHidden(h) {
    if (h === hidden) return;
    hidden = h;
    root.classList.toggle("rn-hidden", h);
    setVars();
  }
  function update() {
    ticking = false;
    const y = Math.max(0, window.scrollY);
    nav.classList.toggle("is-scrolled", y > 8);
    if (y <= height) { setHidden(false); lastY = y; return; }
    const d = y - lastY;
    if (d > STEP) { setHidden(true); lastY = y; }
    else if (d < -STEP) { setHidden(false); lastY = y; }
  }
  window.addEventListener("scroll", () => {
    if (!ticking) { ticking = true; requestAnimationFrame(update); }
  }, { passive: true });
  // Klawiatura: belka wraca, gdy fokus wchodzi do belki.
  nav.addEventListener("focusin", () => setHidden(false));
  const measure = () => { height = nav.offsetHeight; setVars(); };
  if (typeof ResizeObserver !== "undefined") new ResizeObserver(measure).observe(nav);
  else window.addEventListener("resize", measure);
  measure();
  update();
})();
