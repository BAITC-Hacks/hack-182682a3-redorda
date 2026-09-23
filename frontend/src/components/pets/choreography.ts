export const PETS = [
  { name: 'Жан', role: 'Курьер идей', color: '#9d87ed', light: '#d5c8ff', dark: '#7661bd', kind: 'courier', greeting: 'Идеи уже в пути!' },
  { name: 'Дана', role: 'Любит цифры', color: '#76a9f7', light: '#c2dcff', dark: '#5280cc', kind: 'analyst', greeting: 'Всё сходится ♡' },
  { name: 'Айя', role: 'Всегда на связи', color: '#ef9ab8', light: '#ffd5df', dark: '#cd7897', kind: 'caller', greeting: 'Алло, это хорошее настроение!' },
  { name: 'Бек', role: 'Душа команды', color: '#f4c94f', light: '#fff0aa', dark: '#c59b31', kind: 'captain', greeting: 'Ты справишься!' },
] as const;

export type Point = { x: number; y: number };
export type SceneKind = 'document' | 'call' | 'coffee' | 'celebrate';
type Scene = { kind: SceneKind; title: string; positions: Point[]; pair: [number, number]; lines: [string, string] };

// Coordinates are relative to the available viewport, excluding the pet size.
// Every scene starts where the previous one ended, including at the loop seam.
export const SCENES: Scene[] = [
  { kind: 'document', title: 'Жан несёт Дане новую идею', pair: [0, 1], lines: ['Держи идею!', 'Сейчас посмотрю'],
    positions: [{ x: .26, y: .82 }, { x: .40, y: .82 }, { x: .76, y: .50 }, { x: .88, y: .94 }] },
  { kind: 'call', title: 'Айя и Бек на связи', pair: [2, 3], lines: ['Алло, Бек!', 'Да, я рядом!'],
    positions: [{ x: .10, y: .42 }, { x: .48, y: .94 }, { x: .72, y: .75 }, { x: .91, y: .28 }] },
  { kind: 'document', title: 'Дана передаёт Беку заметки', pair: [1, 3], lines: ['Всё записала', 'Забираю!'],
    positions: [{ x: .08, y: .92 }, { x: .57, y: .62 }, { x: .30, y: .30 }, { x: .71, y: .62 }] },
  { kind: 'coffee', title: 'У команды маленький перерыв', pair: [0, 2], lines: ['Пять минут на чай?', 'Я с печеньками'],
    positions: [{ x: .33, y: .90 }, { x: .10, y: .58 }, { x: .47, y: .90 }, { x: .86, y: .92 }] },
  { kind: 'call', title: 'Жан и Айя обсуждают идеи', pair: [0, 2], lines: ['Есть одна идея…', 'Я слушаю!'],
    positions: [{ x: .17, y: .30 }, { x: .63, y: .91 }, { x: .78, y: .38 }, { x: .40, y: .65 }] },
  { kind: 'celebrate', title: 'Все вместе. Всё получится!', pair: [1, 3], lines: ['Команда в сборе!', 'Дай пять!'],
    positions: [{ x: .23, y: .86 }, { x: .39, y: .86 }, { x: .55, y: .86 }, { x: .71, y: .86 }] },
];

export const TRAVEL_MS = 4400;
export const SCENE_MS = 10400;

function spacedPositions(scene: Scene, minimumGap: number) {
  if (scene.kind === 'celebrate' && minimumGap > .16) {
    return scene.positions.map((point, index) => ({ ...point, x: .02 + index * .32 }));
  }
  const positions = scene.positions.map(point => ({ ...point }));
  const [a, b] = scene.pair.map(index => positions[index]);
  if (scene.kind !== 'call' && Math.abs(a.x - b.x) < minimumGap) {
    const middle = Math.min(1 - minimumGap / 2, Math.max(minimumGap / 2, (a.x + b.x) / 2));
    a.x = middle - minimumGap / 2;
    b.x = middle + minimumGap / 2;
  }
  return positions;
}

export function sampleScene(elapsed: number, minimumGap = 0) {
  const index = Math.floor(elapsed / SCENE_MS) % SCENES.length;
  const scene = SCENES[index];
  const previous = SCENES[(index + SCENES.length - 1) % SCENES.length];
  const start = spacedPositions(previous, minimumGap);
  const end = spacedPositions(scene, minimumGap);
  const time = elapsed % SCENE_MS;
  const progress = Math.min(time / TRAVEL_MS, 1);
  const ease = progress * progress * (3 - 2 * progress);
  return {
    index, scene, moving: time < TRAVEL_MS,
    transfer: Math.max(0, Math.min((time - TRAVEL_MS - 900) / 1700, 1)),
    positions: end.map((target, i) => ({
      x: start[i].x + (target.x - start[i].x) * ease,
      y: start[i].y + (target.y - start[i].y) * ease,
      direction: target.x < start[i].x ? -1 : 1,
    })),
  };
}
