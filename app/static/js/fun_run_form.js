/**
 * fun_run_form.js
 * Handles multi-dog Fun Run registration form:
 * - Dynamic add/remove dog blocks
 * - Per-event fee calculation and live total display
 * - Pre-submit validation
 *
 * Requires: window.funRunConfig, window.funRunInitialDogs
 */
(function () {
  var config = window.funRunConfig || {};
  var perEventFee = config.perEventFeeCents || 0;
  var eventList = config.events || [];
  var container = document.getElementById('dogs-container');
  var dogCount = 0;

  // ---- Fee calculation ----

  function computeTotal() {
    var total = 0;
    var selects = document.querySelectorAll('.event-select');
    for (var i = 0; i < selects.length; i++) {
      if (selects[i].value) total += perEventFee;
    }
    return total;
  }

  function updateTotal() {
    var display = document.getElementById('total-display');
    if (display) display.textContent = (computeTotal() / 100).toFixed(2);
  }

  // ---- Build event select element ----

  function buildEventSelect(dogIndex, eventNum, selectedValue) {
    var sel = document.createElement('select');
    sel.name = 'dog_' + dogIndex + '_event_' + eventNum;
    sel.className = 'event-select';
    sel.addEventListener('change', updateTotal);

    var noneOpt = document.createElement('option');
    noneOpt.value = '';
    noneOpt.textContent = '\u2014 no event \u2014';
    sel.appendChild(noneOpt);

    for (var i = 0; i < eventList.length; i++) {
      var opt = document.createElement('option');
      opt.value = eventList[i];
      opt.textContent = eventList[i];
      if (eventList[i] === selectedValue) opt.selected = true;
      sel.appendChild(opt);
    }

    return sel;
  }

  // ---- Build a full dog block ----

  function createDogBlock(index, data) {
    data = data || {};

    var block = document.createElement('div');
    block.className = 'dog-block';
    block.dataset.dogIndex = String(index);

    // Header
    var header = document.createElement('div');
    header.className = 'dog-block-header';

    var title = document.createElement('strong');
    title.textContent = 'Dog ' + (index + 1);
    header.appendChild(title);

    if (index > 0) {
      var removeBtn = document.createElement('button');
      removeBtn.type = 'button';
      removeBtn.className = 'btn-link remove-dog';
      removeBtn.style.marginLeft = '1rem';
      removeBtn.style.color = '#c0392b';
      removeBtn.textContent = 'Remove';
      removeBtn.addEventListener('click', function () {
        block.remove();
        reindexDogs();
        updateTotal();
      });
      header.appendChild(removeBtn);
    }

    block.appendChild(header);

    // Dog name
    var nameGroup = document.createElement('div');
    nameGroup.className = 'form-group';
    var nameLabel = document.createElement('label');
    nameLabel.innerHTML = "Dog's name <span class=\"required\">*</span>";
    var nameInput = document.createElement('input');
    nameInput.type = 'text';
    nameInput.name = 'dog_' + index + '_name';
    nameInput.className = 'dog-name-input';
    if (index === 0) nameInput.required = true;
    nameInput.value = data.dog_name || '';
    nameGroup.appendChild(nameLabel);
    nameGroup.appendChild(nameInput);
    block.appendChild(nameGroup);

    // Breed
    var breedGroup = document.createElement('div');
    breedGroup.className = 'form-group';
    var breedLabel = document.createElement('label');
    breedLabel.innerHTML = 'Breed <span class="form-hint">(optional)</span>';
    var breedInput = document.createElement('input');
    breedInput.type = 'text';
    breedInput.name = 'dog_' + index + '_breed';
    breedInput.value = data.dog_breed || '';
    breedGroup.appendChild(breedLabel);
    breedGroup.appendChild(breedInput);
    block.appendChild(breedGroup);

    // Event selects 1–4 with judged checkbox
    var eventLabels = ['Run 1', 'Run 2 (optional)', 'Run 3 (optional)', 'Run 4 (optional)'];
    for (var e = 1; e <= 4; e++) {
      var evGroup = document.createElement('div');
      evGroup.className = 'form-group entry-row';
      evGroup.style.cssText = 'display:flex; gap:0.75rem; align-items:flex-end; flex-wrap:wrap;';

      var selWrap = document.createElement('div');
      selWrap.style.cssText = 'flex:1; min-width:240px;';
      var evLabel = document.createElement('label');
      evLabel.textContent = eventLabels[e - 1];
      selWrap.appendChild(evLabel);
      selWrap.appendChild(buildEventSelect(index, e, data['event_' + e] || ''));
      evGroup.appendChild(selWrap);

      var judgedLabel = document.createElement('label');
      judgedLabel.style.cssText = 'display:flex; align-items:center; gap:0.4rem; cursor:pointer; font-size:0.9em; padding-bottom:0.4rem; white-space:nowrap;';
      var judgedInput = document.createElement('input');
      judgedInput.type = 'checkbox';
      judgedInput.name = 'dog_' + index + '_event_' + e + '_judged';
      judgedInput.value = '1';
      if (data['event_' + e + '_judged']) judgedInput.checked = true;
      judgedLabel.appendChild(judgedInput);
      judgedLabel.appendChild(document.createTextNode(' Judged'));
      evGroup.appendChild(judgedLabel);

      block.appendChild(evGroup);
    }

    return block;
  }

  // ---- Re-index dog blocks after a removal ----

  function reindexDogs() {
    var blocks = container.querySelectorAll('.dog-block');
    dogCount = blocks.length;
    for (var i = 0; i < blocks.length; i++) {
      var block = blocks[i];
      block.dataset.dogIndex = String(i);

      // Update header title
      var title = block.querySelector('.dog-block-header strong');
      if (title) title.textContent = 'Dog ' + (i + 1);

      // Show/hide remove button for first dog
      var removeBtn = block.querySelector('.remove-dog');
      if (removeBtn) removeBtn.style.display = i === 0 ? 'none' : '';

      // Re-name all inputs
      var inputs = block.querySelectorAll('[name]');
      for (var j = 0; j < inputs.length; j++) {
        inputs[j].name = inputs[j].name.replace(/^dog_\d+_/, 'dog_' + i + '_');
      }
    }
  }

  // ---- Initialize with server-provided data (supports re-render after error) ----

  var initialDogs = window.funRunInitialDogs;
  if (!initialDogs || initialDogs.length === 0) {
    initialDogs = [{}];
  }

  if (container) {
    for (var i = 0; i < initialDogs.length; i++) {
      container.appendChild(createDogBlock(i, initialDogs[i]));
    }
    dogCount = initialDogs.length;
  }

  updateTotal();

  // ---- Add Dog button ----

  var addBtn = document.getElementById('add-dog-btn');
  if (addBtn) {
    addBtn.addEventListener('click', function () {
      if (dogCount >= 10) {
        alert('Maximum of 10 dogs per registration.');
        return;
      }
      if (container) {
        container.appendChild(createDogBlock(dogCount, {}));
        dogCount++;
        updateTotal();
      }
    });
  }

  // ---- Form pre-submit validation ----

  var form = document.getElementById('reg-form');
  if (form) {
    form.addEventListener('submit', function (ev) {
      var firstName = document.querySelector('[name="dog_0_name"]');
      if (!firstName || !firstName.value.trim()) {
        ev.preventDefault();
        alert("Please enter at least one dog's name.");
        return;
      }
      if (computeTotal() === 0) {
        ev.preventDefault();
        alert('Please select at least one event before continuing to payment.');
        return;
      }
    });
  }
})();
