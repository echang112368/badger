/* global React, ReactDOM */

const { useEffect, useMemo, useState } = React;

const currencyFormatter = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

const numberFormatter = new Intl.NumberFormat('en-US');

const percentFormatter = new Intl.NumberFormat('en-US', {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

function formatCurrency(value) {
  if (typeof value !== 'number' || Number.isNaN(value)) {
    return currencyFormatter.format(0);
  }
  return currencyFormatter.format(value);
}

function formatPercent(value) {
  if (typeof value !== 'number' || Number.isNaN(value)) {
    return '0.00%';
  }
  return `${percentFormatter.format(value)}%`;
}

function AffiliateCompaniesTable({ companies, selected, onToggle, onToggleAll }) {
  const allSelected = useMemo(() => {
    if (!companies.length) {
      return false;
    }
    return companies.every((company) => selected.has(company.link_id));
  }, [companies, selected]);

  const anySelected = selected.size > 0;

  return (
    <div className="overflow-x-auto bg-white shadow rounded-md">
      <table className="min-w-full text-sm">
        <thead className="bg-gray-100 text-gray-700">
          <tr>
            <th className="p-3">
              <input
                type="checkbox"
                className="h-4 w-4"
                checked={allSelected}
                onChange={(event) => onToggleAll(event.target.checked)}
              />
            </th>
            <th className="text-left p-3">Business</th>
            <th className="text-right p-3">Monthly Earnings</th>
            <th className="text-right p-3">Total Earnings</th>
            <th className="text-right p-3">Visits</th>
            <th className="text-right p-3">Conversions</th>
            <th className="text-right p-3">Avg. Per Visit</th>
            <th className="text-right p-3">Conversion Rate</th>
          </tr>
        </thead>
        <tbody>
          {companies.length === 0 ? (
            <tr>
              <td colSpan={8} className="p-4 text-center text-gray-500">
                No companies found.
              </td>
            </tr>
          ) : (
            companies.map((company) => {
              const isChecked = selected.has(company.link_id);
              return (
                <tr key={company.link_id} className="odd:bg-white even:bg-gray-50 hover:bg-gray-100">
                  <td className="p-3">
                    <input
                      type="checkbox"
                      className="h-4 w-4"
                      checked={isChecked}
                      onChange={(event) => onToggle(company.link_id, event.target.checked)}
                    />
                  </td>
                  <td className="p-3 text-left">
                    <div className="font-semibold text-gray-900">{company.business}</div>
                    <div className="text-xs text-gray-500">{company.email}</div>
                  </td>
                  <td className="p-3 text-right font-semibold">{formatCurrency(company.monthly_earnings)}</td>
                  <td className="p-3 text-right font-semibold">{formatCurrency(company.total_earnings)}</td>
                  <td className="p-3 text-right">{numberFormatter.format(company.visits)}</td>
                  <td className="p-3 text-right">{numberFormatter.format(company.conversions)}</td>
                  <td className="p-3 text-right">{formatCurrency(company.avg_per_visit)}</td>
                  <td className="p-3 text-right">{formatPercent(company.conversion_rate)}</td>
                </tr>
              );
            })
          )}
        </tbody>
      </table>
      {anySelected ? (
        <div className="px-4 py-2 border-t border-gray-200 bg-gray-50 text-sm text-gray-600">
          {selected.size} compan{selected.size === 1 ? 'y' : 'ies'} selected
        </div>
      ) : null}
    </div>
  );
}

function AffiliateCompaniesApp() {
  const [tab, setTab] = useState('active');
  const [data, setData] = useState({ active: [], inactive: [] });
  const [selected, setSelected] = useState(new Set());
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    const url = window.AFFILIATE_COMPANIES_DATA_URL;
    if (!url) {
      setError('Missing data URL');
      setLoading(false);
      return;
    }

    setLoading(true);
    fetch(url, { headers: { Accept: 'application/json' } })
      .then((response) => {
        if (!response.ok) {
          throw new Error(`Failed to load companies (${response.status})`);
        }
        return response.json();
      })
      .then((payload) => {
        setData({
          active: payload.active || [],
          inactive: payload.inactive || [],
        });
        setLoading(false);
      })
      .catch((err) => {
        setError(err.message || 'Unable to load companies');
        setLoading(false);
      });
  }, []);

  const companies = data[tab] || [];

  useEffect(() => {
    setSelected((prev) => {
      const next = new Set();
      companies.forEach((company) => {
        if (prev.has(company.link_id)) {
          next.add(company.link_id);
        }
      });
      return next;
    });
  }, [tab, companies]);

  const toggleCompany = (linkId, checked) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (checked) {
        next.add(linkId);
      } else {
        next.delete(linkId);
      }
      return next;
    });
  };

  const toggleAll = (checked) => {
    setSelected(() => {
      if (!checked) {
        return new Set();
      }
      return new Set(companies.map((company) => company.link_id));
    });
  };

  const submitBulkDelete = () => {
    if (!selected.size) {
      return;
    }
    if (!window.confirm('Are you sure you want to delete the selected companies?')) {
      return;
    }
    const form = document.getElementById('bulk-delete-form');
    if (!form) {
      return;
    }
    form.querySelectorAll('input[name="selected_links"]').forEach((input) => input.remove());
    selected.forEach((linkId) => {
      const hiddenInput = document.createElement('input');
      hiddenInput.type = 'hidden';
      hiddenInput.name = 'selected_links';
      hiddenInput.value = linkId;
      form.appendChild(hiddenInput);
    });
    form.submit();
  };

  const tabButtonClass = (currentTab) =>
    `px-3 py-2 font-medium text-sm border-b-2 ${
      tab === currentTab ? 'border-blue-500 text-blue-600' : 'border-transparent text-gray-500'
    }`;

  return (
    <div className="space-y-4">
      <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-4">
        <div className="flex space-x-4" role="tablist">
          <button
            type="button"
            className={tabButtonClass('active')}
            onClick={() => setTab('active')}
          >
            Active
          </button>
          <button
            type="button"
            className={tabButtonClass('inactive')}
            onClick={() => setTab('inactive')}
          >
            Inactive
          </button>
        </div>
        <div>
          <button
            type="button"
            className={`px-3 py-2 text-sm rounded-md border ${
              selected.size
                ? 'border-red-500 text-red-600 hover:bg-red-50'
                : 'border-gray-200 text-gray-400 cursor-not-allowed'
            }`}
            onClick={submitBulkDelete}
            disabled={!selected.size}
          >
            Delete Selected
          </button>
        </div>
      </div>

      {loading ? (
        <div className="bg-white shadow rounded-md p-6 text-center text-gray-500">
          Loading companies...
        </div>
      ) : error ? (
        <div className="bg-red-50 border border-red-200 text-red-700 rounded-md p-4">
          {error}
        </div>
      ) : (
        <AffiliateCompaniesTable
          companies={companies}
          selected={selected}
          onToggle={toggleCompany}
          onToggleAll={toggleAll}
        />
      )}
    </div>
  );
}

function initAffiliateCompanies() {
  const container = document.getElementById('affiliate-companies-root');
  if (!container || !ReactDOM || !ReactDOM.createRoot) {
    return;
  }
  const root = ReactDOM.createRoot(container);
  root.render(<AffiliateCompaniesApp />);
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initAffiliateCompanies);
} else {
  initAffiliateCompanies();
}
