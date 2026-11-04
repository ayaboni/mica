/* Sign-in forms marked `data-no-resubmit` are sent with fetch() instead of a normal form post.
 *
 * Why: a normal POST that comes back as a page (wrong password, wrong code, lock-out) leaves a
 * POST entry in the browser history. Pressing Back or Reload later, e.g. after signing in, then
 * shows "Confirm Form Resubmission". Here:
 *   - a redirect (sign-in worked, or the next step such as the e-mailed code) replaces the
 *     login page in history, so Back doesn't land on a stale login form;
 *   - a page (an error message) is shown in place as an ordinary GET entry.
 * Without JavaScript or fetch the form still works as a normal post.
 */
(function () {
  if (!window.fetch || !window.FormData) return;

  document.addEventListener('submit', function (e) {
    var form = e.target;
    if (e.defaultPrevented || !form.hasAttribute || !form.hasAttribute('data-no-resubmit')) return;
    e.preventDefault();

    var data = new FormData(form);
    if (e.submitter && e.submitter.name) data.append(e.submitter.name, e.submitter.value);
    // fetch() would send this as multipart; the views read request.POST, which accepts both,
    // but url-encoding matches what a browser form sends.
    var body = new URLSearchParams();
    data.forEach(function (v, k) { if (typeof v === 'string') body.append(k, v); });

    fetch(form.action, {
      method: 'POST',
      body: body,
      credentials: 'same-origin'
    }).then(function (res) {
      if (res.redirected) {
        window.location.replace(res.url);          // replaces the login page in history
        return null;
      }
      return res.text().then(function (html) {
        history.replaceState(null, '', res.url || window.location.href);
        document.open();
        document.write(html);
        document.close();
      });
    }).catch(function () {
      HTMLFormElement.prototype.submit.call(form);   // network hiccup: fall back to a normal post
    });
  });
})();
