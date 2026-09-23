import { Download } from 'lucide-react';
import type { RunResults as Results } from '../api/types';
import { channelLabels, formatEffect, formatNumber, warningText } from './presentation';

export default function RunResults({ results, exporting, canExport, onExport }: {
  results: Results | null; exporting: boolean; canExport: boolean; onExport: () => void;
}) {
  return <section className="run-results" aria-labelledby="results-heading">
    <div className="section-heading"><div><h2 id="results-heading">Итоговый план кампаний</h2>
      <p>Эффект — прирост выручки за вычетом стоимости контактов.</p></div>
      <button className="button primary" onClick={onExport} disabled={exporting || !canExport}>
        <Download size={17} />{exporting ? 'Скачиваем…' : 'Скачать CSV'}</button></div>
    {!canExport && <p className="subtle">Экспорт пока недоступен на сервере.</p>}
    {!results ? <div className="panel"><p role="status">Ожидаем сохранённые результаты расчёта.</p></div> : <>
      {results.warnings.length > 0 && <div className="notice"><h3>Ограничения прогноза</h3><ul>{results.warnings.map((warning, index) => <li key={index}>{warningText(warning)}</li>)}</ul></div>}
      <div className="effect-summary">
        <div aria-label="Прогнозный эффект"><span>Прогнозный эффект</span><strong>{formatEffect(results.totals.forecast_effect)}</strong><p>Оценка на основе пилотов</p>{results.totals.lower_tail_mean_10 != null && <p>Среднее в нижних 10% модельных исходов: {formatEffect(results.totals.lower_tail_mean_10)}</p>}</div>
        <div aria-label="Результат симуляции"><span>Результат симуляции</span><strong>{formatEffect(results.totals.simulated_effect)}</strong><p>Фактический ответ симулятора</p></div>
      </div>
      {results.campaigns.length === 0 ? <div className="empty"><h3>Подходящих кампаний не найдено</h3>
        <p>Расчёт завершён без выбранных кампаний. Изучите журнал пилотов и попробуйте создать план с другими ограничениями.</p></div> :
        <div className="table-wrap" role="region" aria-label="Таблица кампаний" tabIndex={0}>
          <table className="campaign-table" aria-label="Выбранные кампании"><thead><tr>
            <th scope="col">Кампания и аудитория</th><th scope="col">Предложение</th><th scope="col">Клиенты</th>
            <th scope="col">Расходы</th><th scope="col">Прогнозный эффект</th>
          </tr></thead><tbody>{results.campaigns.map(campaign => <tr key={campaign.id}>
            <td><strong>{campaign.campaign_name}</strong><p>{campaign.audience}</p>
              <details><summary>Почему выбрана</summary><p>{campaign.rationale}</p></details></td>
            <td>{campaign.target_tariff}<small>{channelLabels[campaign.channel]}</small></td>
            <td>{formatNumber(campaign.customers)}</td><td>{formatNumber(campaign.cost)} у. е.</td>
            <td>{formatEffect(campaign.forecast_effect)}</td>
          </tr>)}</tbody></table>
        </div>}
    </>}
  </section>;
}
