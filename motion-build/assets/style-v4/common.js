// Общие хелперы сцен. Все стартовые состояния — немедленным gsap.set, всё движение — в одном paused-таймлайне.
window.__timelines = window.__timelines || {};
const DUR = parseFloat(document.getElementById("root").dataset.duration);
const tl = gsap.timeline({ paused: true });
window.__timelines["main"] = tl;

// Сквозной дрейф: сцена медленно наезжает, пятна ползут, герой плывёт — «ничего не стоит на месте».
gsap.set("#stage", { scale: 1.0 });
tl.to("#stage", { scale: 1.035, duration: DUR, ease: "none" }, 0);
tl.to(".blob", { x: -160, y: 90, duration: DUR, ease: "none" }, 0);
tl.to(".blob2", { x: 140, y: -60, duration: DUR, ease: "none" }, 0);
tl.to(".hero-in", { y: -26, scale: 1.04, duration: DUR, ease: "none" }, 0);

// Кикер уже стоит на кадре 0 — сцена начинается со смысла, а не с пустоты.
gsap.set(".kicker", { opacity: 1 });

// Двухступенчатый вход героя: быстрый влёт expo.out на 80% пути, затем почти линейная досадка; блюр отпускается медленнее движения.
function heroIn(sel, t) {
  gsap.set(sel, { opacity: 0.2, y: 90, scale: 0.9 });
  gsap.set(sel + " img", { filter: "blur(16px)" });
  tl.to(sel, { keyframes: [
      { opacity: 1, y: 14, scale: 0.985, duration: 0.45, ease: "expo.out" },
      { y: 0, scale: 1, duration: 0.8, ease: "none" } ] }, t);
  tl.to(sel + " img", { filter: "blur(0px)", duration: 0.55, ease: "power2.out" }, t);
}

// Мягкий пословный набор: opacity/blur/y, каскад.
function rise(sel, t, stagger = 0.12, dur = 0.5) {
  gsap.set(sel, { opacity: 0, y: 36, filter: "blur(8px)" });
  tl.to(sel, { opacity: 1, y: 0, filter: "blur(0px)", duration: dur, ease: "power3.out", stagger }, t);
}

// Чип: влёт с масштабом и небольшим поворотом, затем досадка.
function chipIn(sel, t, fromY = 30) {
  gsap.set(sel, { opacity: 0, y: fromY, scale: 0.7, rotation: -4 });
  tl.to(sel, { keyframes: [
      { opacity: 1, y: -4, scale: 1.04, rotation: 0.5, duration: 0.28, ease: "back.out(2.2)" },
      { y: 0, scale: 1, rotation: 0, duration: 0.5, ease: "power1.out" } ] }, t);
}

// Счётчик: прокси-объект, состояние живёт в твине — seek-safe.
function countUp(el, from, to, t, dur, fmt) {
  const o = { v: from };
  el.textContent = fmt(from);
  tl.to(o, { v: to, duration: dur, ease: "power2.out", onUpdate: () => { el.textContent = fmt(o.v); } }, t);
}
const rub = (v) => Math.round(v).toLocaleString("ru-RU").replace(/,/g, " ") + " ₽";
