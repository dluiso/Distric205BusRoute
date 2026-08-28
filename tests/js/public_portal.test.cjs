const test = require('node:test');
const assert = require('node:assert/strict');

const {
  normalizeSearch,
  matchesSearch,
  cardPriority,
  sortCards,
  groupKey,
} = require('../../static/js/public_portal.js');

function card(id, identifier, name, isDefault = true) {
  return {
    dataset: {
      busId: String(id),
      identifier,
      name,
      defaultStatus: isDefault ? '1' : '0',
    },
  };
}

test('search normalizes separators, compact identifiers, accents, and token order', () => {
  const values = ['TT', '01', 'Ruta Española'];
  assert.equal(matchesSearch(values, 'TT-01'), true);
  assert.equal(matchesSearch(values, 'tt01'), true);
  assert.equal(matchesSearch(values, '01 TT'), true);
  assert.equal(matchesSearch(values, 'ruta espanola'), true);
  assert.equal(matchesSearch(values, 'TR-01'), false);
  assert.deepEqual(normalizeSearch(' TT—01 '), {spaced: 'tt 01', compact: 'tt01'});
});

test('attention and favorites determine priority before natural bus order', () => {
  const cards = [
    card(1, 'TT', '10', true),
    card(2, 'TT', '2', true),
    card(3, 'TR', '8', false),
    card(4, 'TR', '3', false),
  ];
  const favorites = new Set(['1', '3']);
  assert.equal(cardPriority(cards[2], favorites), 0);
  assert.equal(cardPriority(cards[3], favorites), 1);
  assert.equal(cardPriority(cards[0], favorites), 2);
  assert.equal(cardPriority(cards[1], favorites), 3);
  assert.deepEqual(
    sortCards(cards, favorites, 'en-US').map((item) => item.dataset.busId),
    ['3', '4', '1', '2'],
  );
});

test('natural order treats bus numbers numerically', () => {
  const cards = [card(1, 'TT', '10'), card(2, 'TT', '2'), card(3, 'TT', '1')];
  assert.deepEqual(
    sortCards(cards, new Set(), 'en-US').map((item) => item.dataset.busId),
    ['3', '2', '1'],
  );
});

test('mobile visual groups keep affected buses ahead of favorites and others', () => {
  const favorites = new Set(['2']);
  assert.equal(groupKey(card(1, 'TR', '8', false), favorites, false, false), 'affected');
  assert.equal(groupKey(card(2, 'TT', '2', true), favorites, false, false), 'favorites');
  assert.equal(groupKey(card(3, 'TT', '10', true), favorites, false, false), 'other');
  assert.equal(groupKey(card(3, 'TT', '10', true), favorites, true, false), 'affected');
  assert.equal(groupKey(card(3, 'TT', '10', true), favorites, false, true), 'favorites');
});
