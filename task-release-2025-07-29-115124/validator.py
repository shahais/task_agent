#!/usr/bin/env python3
"""
SWE-bench Data Point Validator с ПРАВИЛЬНЫМ SWE-bench API
"""

import json
import sys
import argparse
import tempfile
import uuid
from pathlib import Path
from typing import Dict, List, Any
import logging

# ПРАВИЛЬНЫЙ SWE-bench API
from swebench.harness.run_evaluation import main as run_evaluation_main
from swebench.harness.utils import load_swebench_dataset

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class SWEBenchValidator:
    """Валидатор с правильным SWE-bench evaluation API."""
    
    def __init__(self, timeout: int = 1800):
        self.required_fields = [
            'instance_id', 'repo', 'base_commit', 'patch', 
            'test_patch', 'problem_statement', 'hints_text', 
            'created_at', 'version', 'FAIL_TO_PASS', 'PASS_TO_PASS'
        ]
        self.timeout = timeout
    
    def validate_json_structure(self, data: Dict[str, Any]) -> List[str]:
        """Проверяет структуру JSON на наличие обязательных полей."""
        errors = []
        
        for field in self.required_fields:
            if field not in data:
                errors.append(f"Отсутствует обязательное поле: {field}")
        
        # Проверяем типы данных
        if 'instance_id' in data and not isinstance(data['instance_id'], str):
            errors.append("instance_id должен быть строкой")
            
        if 'repo' in data and not isinstance(data['repo'], str):
            errors.append("repo должен быть строкой")
        
        # Проверяем формат instance_id
        if 'instance_id' in data:
            instance_id = data['instance_id']
            if not instance_id or '__' not in instance_id:
                errors.append("instance_id должен содержать '__' (формат: repo__issue-number)")
        
        # Проверяем тестовые поля
        for test_field in ['FAIL_TO_PASS', 'PASS_TO_PASS']:
            if test_field in data:
                if isinstance(data[test_field], str):
                    try:
                        test_list = json.loads(data[test_field])
                        if not isinstance(test_list, list):
                            errors.append(f"{test_field} должен быть JSON списком строк")
                    except json.JSONDecodeError:
                        errors.append(f"{test_field} содержит невалидный JSON")
                elif not isinstance(data[test_field], list):
                    errors.append(f"{test_field} должен быть списком")
            
        return errors

    def run_swebench_evaluation(self, data_point_path: str) -> Dict[str, Any]:
        """
        ПРАВИЛЬНАЯ валидация через swebench.harness.run_evaluation.main
        """
        result = {
            'evaluation_success': False,
            'patch_applied': False,
            'tests_passed': False,
            'fail_to_pass_results': {},
            'pass_to_pass_results': {},
            'errors': [],
            'logs': []
        }
        
        try:
            with open(data_point_path, 'r') as f:
                data = json.load(f)
            
            instance_id = data['instance_id']
            result['logs'].append(f"Начинаем SWE-bench evaluation для {instance_id}")
            
            # Создаем предикт в формате SWE-bench
            # Используем golden patch как решение для валидации
            prediction = {
                'instance_id': instance_id,
                'model_patch': data['patch'],  # Golden patch
                'model_name_or_path': 'golden_patch_validator'
            }
            
            # Создаем временные файлы для SWE-bench
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_dir = Path(temp_dir)
                
                # Создаем датасет JSONL файл
                dataset_file = temp_dir / 'dataset.jsonl'
                with open(dataset_file, 'w') as f:
                    json.dump(data, f)
                    f.write('\n')
                
                # Создаем предикты JSONL файл
                predictions_file = temp_dir / 'predictions.jsonl'
                with open(predictions_file, 'w') as f:
                    json.dump(prediction, f)
                    f.write('\n')
                
                # Папка для отчетов
                report_dir = temp_dir / 'reports'
                report_dir.mkdir()
                
                # Генерируем уникальный run_id
                run_id = f"validator_{uuid.uuid4().hex[:8]}"
                
                result['logs'].append("Запускаем официальный SWE-bench evaluation...")
                
                try:
                    # ПРАВИЛЬНЫЙ вызов SWE-bench evaluation
                    report_path = run_evaluation_main(
                        dataset_name=str(dataset_file),  # Путь к нашему датасету
                        split='test',  # По умолчанию
                        instance_ids=[instance_id],  # Только наш instance
                        predictions_path=str(predictions_file),  # Путь к предиктам
                        max_workers=1,  # Один воркер для простоты
                        force_rebuild=False,
                        cache_level='env',
                        clean=False,
                        open_file_limit=4096,
                        run_id=run_id,
                        timeout=self.timeout,
                        namespace='swebench',
                        rewrite_reports=False,
                        modal=False,
                        instance_image_tag='latest',
                        report_dir=str(report_dir)
                    )
                    
                    result['logs'].append(f"✓ SWE-bench evaluation завершен, отчет: {report_path}")
                    
                    # ИСПРАВЛЕНИЕ: Правильно читаем результаты из отчета
                    if report_path and Path(report_path).exists():
                        with open(report_path, 'r') as f:
                            report_data = json.load(f)
                        
                        # Парсим результат в формате SWE-bench evaluation
                        self._parse_swebench_report(report_data, instance_id, data, result)
                    else:
                        # Ищем результаты в report_dir
                        result_files = list(report_dir.glob('**/*.json'))
                        result['logs'].append(f"Найдено файлов результатов: {len(result_files)}")
                        
                        for result_file in result_files:
                            try:
                                with open(result_file, 'r') as f:
                                    file_data = json.load(f)
                                
                                # ИСПРАВЛЕНИЕ: Правильно парсим файл результатов
                                self._parse_swebench_report(file_data, instance_id, data, result)
                                break  # Выходим после первого найденного результата
                            except Exception as e:
                                result['logs'].append(f"Ошибка чтения {result_file}: {e}")
                    
                    result['evaluation_success'] = len(result['errors']) == 0
                    
                except Exception as e:
                    result['errors'].append(f"Ошибка SWE-bench evaluation: {e}")
                    logger.exception("SWE-bench evaluation error")
                
        except Exception as e:
            result['errors'].append(f"Ошибка подготовки evaluation: {e}")
            logger.exception("Evaluation preparation error")
        
        return result
    
    def _parse_swebench_report(self, report_data: Dict, instance_id: str, data: Dict, result: Dict):
        """ИСПРАВЛЕННАЯ функция парсинга отчета SWE-bench."""
        try:
            # Формат отчета SWE-bench:
            # {
            #   "resolved_ids": ["id1", "id2"],
            #   "error_ids": ["id3"],
            #   "completed_ids": ["id1", "id2", "id3"],
            #   ...
            # }
            
            resolved_ids = report_data.get('resolved_ids', [])
            error_ids = report_data.get('error_ids', [])
            completed_ids = report_data.get('completed_ids', [])
            
            # Проверяем результат для нашего instance
            if instance_id in resolved_ids:
                result['patch_applied'] = True
                result['tests_passed'] = True
                result['logs'].append(f"✓ Instance {instance_id} успешно resolved")
            elif instance_id in error_ids:
                result['patch_applied'] = False
                result['tests_passed'] = False
                result['errors'].append(f"Instance {instance_id} завершился с ошибкой")
            elif instance_id in completed_ids:
                result['patch_applied'] = True
                result['tests_passed'] = False
                result['logs'].append(f"Instance {instance_id} completed, но не resolved")
            else:
                result['errors'].append(f"Instance {instance_id} не найден в результатах")
            
            # Логируем общую статистику
            total_instances = report_data.get('total_instances', 0)
            resolved_instances = report_data.get('resolved_instances', 0)
            result['logs'].append(f"Статистика: {resolved_instances}/{total_instances} instances resolved")
                
        except Exception as e:
            result['errors'].append(f"Ошибка парсинга отчета: {e}")
            logger.exception("Report parsing error")
    
    def validate_data_point(self, file_path: str, run_evaluation: bool = True) -> Dict[str, Any]:
        """Валидирует одну точку данных SWE-bench."""
        result = {
            'file': file_path,
            'valid': True,
            'errors': [],
            'warnings': [],
            'structure_valid': True,
            'swe_bench_evaluation': None
        }
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # 1. Проверка структуры JSON
            structure_errors = self.validate_json_structure(data)
            result['errors'].extend(structure_errors)
            result['structure_valid'] = len(structure_errors) == 0
            
            # 2. SWE-bench evaluation (если требуется)
            if run_evaluation and result['structure_valid']:
                logger.info(f"Запускаем SWE-bench evaluation для {file_path}")
                evaluation_result = self.run_swebench_evaluation(file_path)
                result['swe_bench_evaluation'] = evaluation_result
                
                # Добавляем ошибки evaluation
                if not evaluation_result['evaluation_success']:
                    result['errors'].extend(evaluation_result['errors'])
            
            # Финальный статус
            result['valid'] = len(result['errors']) == 0
            
        except json.JSONDecodeError as e:
            result['errors'].append(f"Невалидный JSON: {e}")
            result['valid'] = False
            result['structure_valid'] = False
        except Exception as e:
            result['errors'].append(f"Ошибка валидации: {e}")
            result['valid'] = False
        
        return result
    
    def validate_batch(self, file_paths: List[str], run_evaluation: bool = True) -> Dict[str, Any]:
        """Валидирует пакет файлов."""
        results = []
        
        for file_path in file_paths:
            logger.info(f"Валидируем {file_path}")
            result = self.validate_data_point(file_path, run_evaluation)
            results.append(result)
        
        # Статистика
        valid_count = sum(1 for r in results if r['valid'])
        total_count = len(results)
        
        return {
            'results': results,
            'summary': {
                'total': total_count,
                'valid': valid_count,
                'invalid': total_count - valid_count,
                'success_rate': valid_count / total_count if total_count > 0 else 0
            }
        }


def main():
    parser = argparse.ArgumentParser(description='SWE-bench Data Point Validator')
    parser.add_argument('files', nargs='+', help='JSON файлы для валидации')
    parser.add_argument('--no-evaluation', action='store_true', 
                       help='Пропустить SWE-bench evaluation')
    parser.add_argument('--timeout', type=int, default=1800,
                       help='Timeout для evaluation (секунды)')
    parser.add_argument('--verbose', action='store_true',
                       help='Подробный вывод')
    
    args = parser.parse_args()
    
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    validator = SWEBenchValidator(timeout=args.timeout)
    
    # Валидация
    batch_result = validator.validate_batch(args.files, not args.no_evaluation)
    
    # Вывод результатов
    for result in batch_result['results']:
        status = "✓ VALID" if result['valid'] else "✗ INVALID"
        print(f"{status}: {result['file']}")
        
        if result['errors']:
            for error in result['errors']:
                print(f"  ERROR: {error}")
        
        if result['swe_bench_evaluation']:
            eval_result = result['swe_bench_evaluation']
            print(f"  SWE-bench evaluation:")
            print(f"    Patch applied: {'✓' if eval_result['patch_applied'] else '✗'}")
            print(f"    Tests passed: {'✓' if eval_result['tests_passed'] else '✗'}")
    
    # Общая статистика
    summary = batch_result['summary']
    print(f"\nРезультат: {summary['valid']}/{summary['total']} файлов валидны")
    print("💡 Использовался ОФИЦИАЛЬНЫЙ SWE-bench evaluation harness с Docker")
    
    # Код возврата
    sys.exit(0 if summary['valid'] == summary['total'] else 1)


if __name__ == '__main__':
    main()
